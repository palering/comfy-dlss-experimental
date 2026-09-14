cmake_minimum_required(VERSION 3.24)
project(comfy_dlss_sdk_worker LANGUAGES CXX)

# Optional Windows SDK-enabled build of the SAME owned Worker. The ordinary
# Zig NR build remains independent and reports SR compiled=false via CSR1.
if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "The optional SDK Worker requires Windows x64 and an MSVC-compatible compiler/runtime.")
endif()
set(COMFY_NGX_SDK_ROOT "" CACHE PATH "Already-downloaded official NVIDIA DLSS SDK root")
set(COMFY_NGX_SDK_LIBRARY "${COMFY_NGX_SDK_ROOT}/lib/Windows_x86_64/x64/nvsdk_ngx_d.lib" CACHE FILEPATH "Official dynamic-runtime NGX SDK archive")
if(NOT EXISTS "${COMFY_NGX_SDK_ROOT}/include/nvsdk_ngx_helpers.h" OR NOT EXISTS "${COMFY_NGX_SDK_LIBRARY}")
  message(FATAL_ERROR "Supply COMFY_NGX_SDK_ROOT and, when needed, COMFY_NGX_SDK_LIBRARY. Nothing is downloaded automatically.")
endif()

function(comfy_owned_target target)
  target_compile_features(${target} PRIVATE cxx_std_20)
  target_include_directories(${target} PRIVATE src)
  target_include_directories(${target} SYSTEM PRIVATE "${COMFY_NGX_SDK_ROOT}/include")
  target_compile_options(${target} PRIVATE /W4 /WX /permissive- /EHsc)
  target_compile_definitions(${target} PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
  # NVIDIA's _d archive denotes dynamic CRT, not debug CRT. Match /MD even for
  # Debug configurations; never mix it with a guessed MinGW CRT substitute.
  set_property(TARGET ${target} PROPERTY MSVC_RUNTIME_LIBRARY MultiThreadedDLL)
endfunction()

add_library(comfy_dlss_sr_engine STATIC src/sr_engine.cpp)
comfy_owned_target(comfy_dlss_sr_engine)
target_link_libraries(comfy_dlss_sr_engine PUBLIC "${COMFY_NGX_SDK_LIBRARY}" dxguid ws2_32)

add_executable(comfy-dlss-worker src/nr_probe.cpp src/nr_engine.cpp src/sr_service.cpp
  src/nr_parameters.cpp src/nr_parameter_memory.cpp tests/nr_parameter_abi_check.cpp)
comfy_owned_target(comfy-dlss-worker)
target_compile_definitions(comfy-dlss-worker PRIVATE COMFY_DLSS_ENABLE_SR)
# Only NR uses the snippet ABI. The public SDK SR translation unit must not.
set_source_files_properties(src/nr_engine.cpp PROPERTIES COMPILE_DEFINITIONS NGX_SNIPPET_BUILD)
target_link_libraries(comfy-dlss-worker PRIVATE comfy_dlss_sr_engine ws2_32 dxguid)

add_library(caller SHARED src/nr_caller.cpp)
comfy_owned_target(caller)
target_compile_definitions(caller PRIVATE NGX_SNIPPET_BUILD)
target_compile_options(caller PRIVATE /Ob0 /GL-)
set_target_properties(caller PROPERTIES OUTPUT_NAME nvngx
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/caller")

enable_testing()
foreach(contract sr_input_contract sr_wire owned_wire nr_sequence_contract)
  add_executable(${contract}_test tests/${contract}_test.cpp)
  comfy_owned_target(${contract}_test)
  target_compile_options(${contract}_test PRIVATE /UNDEBUG)
  add_test(NAME ${contract} COMMAND ${contract}_test)
endforeach()
add_test(NAME nr_parameter_abi COMMAND comfy-dlss-worker --self-test-parameter-abi)
