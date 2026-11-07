serenity_component(Installer TARGETS Installer)
set(SOURCES main.cpp)
serenity_app(Installer ICON app-partition-editor)
target_link_libraries(Installer PRIVATE LibCore LibCrypto LibFileSystem LibGfx LibGUI LibMain)
