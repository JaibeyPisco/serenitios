/*
 * Copyright (c) 2020, Peter Elliott <pelliott@serenityos.org>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Base64.h>
#include <AK/Types.h>
#include <LibCrypto/Authentication/HMAC.h>
#include <LibCrypto/Hash/SHA2.h>
#include "crypt.h"
#include <errno.h>
#include <string.h>

extern "C" {

static struct crypt_data crypt_data;

char* crypt(char const* key, char const* salt)
{
    crypt_data.initialized = true;
    return crypt_r(key, salt, &crypt_data);
}

static constexpr size_t crypt_salt_max = 16;

char* crypt_r(char const* key, char const* salt, struct crypt_data* data)
{
    if (!data->initialized) {
        errno = EINVAL;
        return nullptr;
    }

    // Compact PBKDF2-HMAC-SHA256: 96-bit random ASCII salt, 192-bit verifier.
    // Fits the existing crypt_data ABI. Legacy $5$ remains readable below.
    // RFC 8018 section 5.2, one 32-byte block, truncated to 24 bytes.
    if (StringView { salt, strlen(salt) }.starts_with("$p$600000$"sv)) {
        auto const* value = salt + 10;
        if (strcspn(value, "$") != 16) {
            errno = EINVAL;
            return nullptr;
        }
        for (size_t i = 0; i < 16; ++i) {
            if (!is_ascii_alphanumeric(value[i]) && value[i] != '+' && value[i] != '/') {
                errno = EINVAL;
                return nullptr;
            }
        }
        Crypto::Authentication::HMAC<Crypto::Hash::SHA256> prf(StringView { key, strlen(key) });
        prf.update(StringView { value, 16 });
        u8 const block[] = { 0, 0, 0, 1 };
        prf.update(ReadonlyBytes { block, sizeof(block) });
        auto u = prf.digest();
        auto result = u;
        for (size_t iteration = 1; iteration < 600000; ++iteration) {
            u = prf.process(u.bytes());
            for (size_t i = 0; i < 32; ++i)
                result.data[i] ^= u.data[i];
        }
        auto encoded = encode_base64(ReadonlyBytes { result.immutable_data(), 24 });
        if (encoded.is_error()) {
            errno = ENOMEM;
            return nullptr;
        }
        memcpy(data->result, salt, 26);
        data->result[26] = '$';
        memcpy(data->result + 27, encoded.value().bytes_as_string_view().characters_without_null_termination(), 32);
        data->result[59] = '\0';
        return data->result;
    }

    // We only support SHA-256 at the moment
    if (strlen(salt) < 3 || salt[0] != '$' || salt[1] != '5' || salt[2] != '$') {
        errno = EINVAL;
        return nullptr;
    }

    char const* salt_value = salt + 3;
    size_t salt_len = min(strcspn(salt_value, "$"), crypt_salt_max);
    size_t header_len = salt_len + 3;

    bool fits = ByteString(salt, header_len).copy_characters_to_buffer(data->result, sizeof(data->result));
    if (!fits) {
        errno = EINVAL;
        return nullptr;
    }
    data->result[header_len] = '$';

    Crypto::Hash::SHA256 sha;
    sha.update(StringView { key, strlen(key) });
    sha.update(reinterpret_cast<u8 const*>(salt_value), salt_len);

    auto digest = sha.digest();
    auto string_or_error = encode_base64({ digest.immutable_data(), digest.data_length() });
    if (string_or_error.is_error()) {
        errno = ENOMEM;
        return nullptr;
    }

    auto string = string_or_error.value().bytes_as_string_view();
    fits = string.copy_characters_to_buffer(data->result + header_len + 1, sizeof(data->result) - header_len - 1);
    if (!fits) {
        errno = EINVAL;
        return nullptr;
    }

    return data->result;
}
}
