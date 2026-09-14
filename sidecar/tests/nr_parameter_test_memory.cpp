#include <atomic>
#include <cstdlib>
namespace {
std::atomic<bool> fail_next{false};
std::atomic<unsigned int> live{0};
}
extern "C" void ComfyNR_TestFailNextAllocation() { fail_next.store(true); }
extern "C" unsigned int ComfyNR_TestOutstandingAllocations() { return live.load(); }
extern "C" void* ComfyNR_AllocateParameterStorage(std::size_t bytes) {
    if (fail_next.exchange(false)) return nullptr;
    void* memory = std::malloc(bytes);
    if (memory) live.fetch_add(1);
    return memory;
}
extern "C" void ComfyNR_FreeParameterStorage(void* memory) {
    if (memory) live.fetch_sub(1);
    std::free(memory);
}
