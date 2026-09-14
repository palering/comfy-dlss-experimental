#include <cstdlib>
// This host TU and all consumers exchange only C types. Allocation/free always
// use the same module's allocator, never a guessed MSVC runtime substitute.
extern "C" void* ComfyNR_AllocateParameterStorage(std::size_t size) { return std::malloc(size); }
extern "C" void ComfyNR_FreeParameterStorage(void* pointer) { std::free(pointer); }
