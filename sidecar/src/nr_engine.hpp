#pragma once
#include <filesystem>
#include <memory>
#include <span>

namespace comfy_dlss {
class probe_report;
namespace owned_wire { struct settings; }
// No model/Parameter C++ ABI is exposed to consumers of this host interface.
class nr_engine final {
    class implementation;
    std::unique_ptr<implementation> implementation_;
public:
    nr_engine(unsigned int width,unsigned int height,probe_report& report);
    ~nr_engine();
    nr_engine(const nr_engine&)=delete;
    nr_engine& operator=(const nr_engine&)=delete;
    void initialize_device();
    void initialize(const std::filesystem::path& model,const std::filesystem::path& caller,
                    const std::filesystem::path& logs,bool init_only=false,const owned_wire::settings* look=nullptr);
    unsigned long long evaluate(unsigned int count);
    void evaluate_frame(std::span<const unsigned char> color,std::span<const unsigned char> motion,
                        unsigned int frame,bool reset,std::span<unsigned char> result);
};
}
