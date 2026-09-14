#pragma once
#include <array>
#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <span>
#include <stdexcept>
#include <string_view>

namespace comfy_dlss::owned_wire {
constexpr std::uint32_t magic = 0x31524e43, version = 1;
constexpr std::size_t header_bytes = 32;
constexpr std::uint32_t max_payload = 1920U*1080U*12U+16U;
enum class type : std::uint32_t { create=1, frame=2, end=3, release=4, shutdown=5, ping=6,
                                caps=128, ack=129, result=130, error=131 };
// Optional error payload only. The CNR1 handshake, success packets and old
// four-byte errors remain unchanged; old clients still fail closed on ERROR.
constexpr std::uint32_t error_magic=0x31524543, error_version=1;
constexpr std::size_t error_header_bytes=40, max_error_bytes=256;
enum class error_stage : std::uint32_t { unknown, protocol, device, load, init, resources,
    create, create_fence, evaluate, evaluate_fence, readback, release, shutdown, transport };
enum class error_domain : std::uint32_t { worker, ngx, hresult, win32, winsock, wait };
struct error_info {
    error_stage stage=error_stage::unknown;
    error_domain domain=error_domain::worker;
    std::uint32_t code=1;
    std::uint64_t frame=0, evaluation=0;
    std::string_view message="Worker operation failed";
};
inline std::uint32_t get32(std::span<const unsigned char> data, std::size_t offset) {
    if (offset > data.size() || data.size()-offset < 4) throw std::runtime_error("Truncated wire field");
    std::uint32_t value=0;
    for (unsigned int i=0;i<4;++i) value |= static_cast<std::uint32_t>(data[offset+i]) << (8*i);
    return value;
}
inline std::uint64_t get64(std::span<const unsigned char> data, std::size_t offset) {
    const auto low=get32(data,offset); return low | (static_cast<std::uint64_t>(get32(data,offset+4)) << 32);
}
inline void put32(std::span<unsigned char> data, std::size_t offset, std::uint32_t value) {
    if (offset > data.size() || data.size()-offset < 4) throw std::runtime_error("Short wire output");
    for (unsigned int i=0;i<4;++i) data[offset+i]=static_cast<unsigned char>((value >> (8*i)) & 255);
}
inline void put64(std::span<unsigned char> data, std::size_t offset, std::uint64_t value) {
    put32(data,offset,static_cast<std::uint32_t>(value & 0xffffffffULL)); put32(data,offset+4,static_cast<std::uint32_t>(value >> 32));
}
inline std::size_t encode_error(std::span<unsigned char> data, const error_info& error) {
    if (error.message.empty() || error.message.size()>max_error_bytes-error_header_bytes ||
        data.size()<error_header_bytes+error.message.size()) throw std::runtime_error("Invalid error output buffer");
    for (const auto c : error.message)
        if (c<32 || c>126) throw std::runtime_error("Invalid error message literal");
    put32(data,0,error_magic); put32(data,4,error_version);
    put32(data,8,static_cast<std::uint32_t>(error.stage)); put32(data,12,static_cast<std::uint32_t>(error.domain));
    put32(data,16,error.code); put32(data,20,static_cast<std::uint32_t>(error.message.size()));
    put64(data,24,error.frame); put64(data,32,error.evaluation);
    for (std::size_t i=0;i<error.message.size();++i) data[error_header_bytes+i]=static_cast<unsigned char>(error.message[i]);
    return error_header_bytes+error.message.size();
}
inline error_stage stage_from_event(std::string_view stage, error_stage fallback) noexcept {
    constexpr std::array<std::string_view,14> names{"unknown","protocol","device","load","init","resources",
        "create","create_fence","evaluate","evaluate_fence","readback","release","shutdown","transport"};
    for (std::size_t i=0;i<names.size();++i) if (stage==names[i]) return static_cast<error_stage>(i);
    return fallback;
}
// Never copy exception.what(): filesystem exceptions can contain local paths.
// Only fixed known operation names and strictly parsed native hex codes escape.
inline error_info describe_exception(std::string_view what, error_info error) noexcept {
    constexpr std::array<std::string_view,22> hresult_operations{
        "CommandList.Close","Allocator.Reset","CommandList.Reset","Create texture","Create transfer",
        "Map upload","Map frame upload","CreateDXGIFactory1","EnumAdapters1","GetDesc1","Create queue",
        "Create allocator","Create command list","Create fence","Map readback","Map sequence output",
        "Worker socket timeout setup failed","Worker socket failed","Worker TCP_NODELAY failed",
        "Worker socket mode failed","Worker connect failed","Worker transport failed"};
    const auto separator=what.find(": 0x");
    if (separator!=std::string_view::npos && what.size()==separator+12) {
        const auto operation=what.substr(0,separator);
        error_domain domain=error_domain::worker;
        bool known=false;
        if (operation=="InitParameters" || operation=="CreateFeature18" ||
            operation=="EvaluateFeature18" || operation=="Evaluate sequence frame") {
            known=true; domain=error_domain::ngx;
        } else if (operation=="LoadLibraryEx failed") {
            known=true; domain=error_domain::win32; error.stage=error_stage::load;
        } else for (const auto name : hresult_operations) if (operation==name) {
            known=true; domain=operation.starts_with("Worker ") ? error_domain::winsock : error_domain::hresult;
            if (operation.starts_with("Map ") && operation!="Map upload" && operation!="Map frame upload") error.stage=error_stage::readback;
            break;
        }
        if (known) {
            std::uint32_t code=0;
            for (const auto c : what.substr(separator+4)) {
                unsigned int digit=16;
                if(c>='0' && c<='9') digit=static_cast<unsigned int>(c-'0');
                else if(c>='a' && c<='f') digit=static_cast<unsigned int>(c-'a')+10;
                else if(c>='A' && c<='F') digit=static_cast<unsigned int>(c-'A')+10;
                if(digit>15) return error;
                code=(code<<4)|digit;
            }
            error.domain=domain; error.code=code; error.message=domain==error_domain::ngx ? "NGX operation failed" :
                domain==error_domain::win32 ? "Native library load failed" : domain==error_domain::winsock ?
                "Worker socket operation failed" : "D3D12 operation failed";
            return error;
        }
    }
    if(what=="Caller ABI mismatch") error.message="Caller ABI mismatch; rebuild the matching Worker and caller";
    else if(what.starts_with("Missing export: ComfyNR_")) error.message="Required caller export missing; use the matching thin caller";
    else if(what.starts_with("Missing export: NVSDK_")) error.message="Required NGX export missing from the selected runtime";
    else if(what=="No NVIDIA D3D12 hardware adapter") { error.stage=error_stage::device; error.message="No NVIDIA D3D12 hardware adapter"; }
    else if(what=="Non-finite sequence output" || what=="NR output contains non-finite FP16 values") {
        error.stage=error_stage::readback; error.message="NR output contains non-finite values";
    } else if(error.stage==error_stage::protocol) error.message="Worker rejected request state or input contract";
    else if(error.stage==error_stage::transport) error.message="Worker transport closed or timed out";
    return error;
}
struct header { type kind; std::uint32_t bytes; std::uint64_t request, session; };
inline header parse_header(std::span<const unsigned char> data) {
    if (data.size()!=header_bytes || get32(data,0)!=magic || get32(data,4)!=version || get32(data,12)>max_payload)
        throw std::runtime_error("Invalid owned protocol header");
    const auto kind=get32(data,8);
    if (!((kind>=1 && kind<=6) || (kind>=128 && kind<=131))) throw std::runtime_error("Unknown owned message");
    return {static_cast<type>(kind),get32(data,12),get64(data,16),get64(data,24)};
}
inline std::array<unsigned char,header_bytes> encode_header(header h) {
    std::array<unsigned char,header_bytes> data{};
    put32(data,0,magic); put32(data,4,version); put32(data,8,static_cast<std::uint32_t>(h.kind));
    put32(data,12,h.bytes); put64(data,16,h.request); put64(data,24,h.session); return data;
}
struct settings {
    std::uint32_t width,height,warmup,preset,style,auto_mask,ui;
    float intensity,tone,structure,skin;
};
inline settings parse_settings(std::span<const unsigned char> data) {
    if (data.size()!=48 || get32(data,28)) throw std::runtime_error("Invalid owned settings length/reserved");
    settings s{get32(data,0),get32(data,4),get32(data,8),get32(data,12),get32(data,16),get32(data,20),get32(data,24),
        std::bit_cast<float>(get32(data,32)),std::bit_cast<float>(get32(data,36)),
        std::bit_cast<float>(get32(data,40)),std::bit_cast<float>(get32(data,44))};
    if (s.width<64 || s.width>1920 || s.height<64 || s.height>1080 || s.warmup>240 || s.preset>3 || s.style>3 || s.auto_mask>1 || s.ui>1)
        throw std::runtime_error("Owned settings outside bounds");
    for (float v : {s.intensity,s.tone,s.structure})
        if (!std::isfinite(v) || v<0 || v>3) throw std::runtime_error("Invalid owned strength");
    if (!std::isfinite(s.skin) || s.skin< -1 || s.skin>3) throw std::runtime_error("Invalid owned skin strength");
    return s;
}
}
