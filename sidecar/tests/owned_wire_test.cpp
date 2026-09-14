#include "owned_wire.hpp"
#include <cassert>
using namespace comfy_dlss::owned_wire;
template<class F> void rejected(F f) { bool failed=false; try { f(); } catch(const std::runtime_error&) { failed=true; } assert(failed); }
int main() {
    const auto bytes=encode_header({type::frame,48,0x123456789abcdef0ULL,11});
    assert(bytes[0]=='C' && bytes[1]=='N' && bytes[2]=='R' && bytes[3]=='1');
    const auto header=parse_header(bytes);
    assert(header.kind==type::frame && header.request==0x123456789abcdef0ULL && header.session==11 && header.bytes==48);
    rejected([&]{parse_header(std::span(bytes).first(31));});
    for (auto offset : {0U,4U,8U,12U}) { auto bad=bytes; put32(bad,offset,0xffffffff); rejected([&]{parse_header(bad);}); }
    std::array<unsigned char,48> settings{};
    put32(settings,0,640);put32(settings,4,360);put32(settings,8,1);put32(settings,20,1);
    for (auto offset : {32U,36U,40U}) put32(settings,offset,std::bit_cast<std::uint32_t>(1.f));
    put32(settings,44,std::bit_cast<std::uint32_t>(-1.f));
    const auto parsed=parse_settings(settings);
    assert(parsed.width==640 && parsed.height==360 && parsed.warmup==1 && parsed.skin==-1);
    for(auto offset : {0U,4U,8U,12U,16U,20U,24U,28U,32U,36U,40U,44U}) {
        auto bad=settings; put32(bad,offset,0xffffffff); rejected([&]{parse_settings(bad);});
    }
    rejected([&]{get32(settings,48);}); rejected([&]{put64(settings,44,1);});

    std::array<unsigned char,max_error_bytes> diagnostic{};
    const error_info failure{error_stage::evaluate,error_domain::ngx,0xbad00002,3,5,"NGX operation failed"};
    const auto size=encode_error(diagnostic,failure);
    assert(size==40+failure.message.size());
    assert(get32(diagnostic,0)==error_magic && get32(diagnostic,4)==1);
    assert(get32(diagnostic,8)==8 && get32(diagnostic,12)==1 && get32(diagnostic,16)==0xbad00002);
    assert(get32(diagnostic,20)==failure.message.size() && get64(diagnostic,24)==3 && get64(diagnostic,32)==5);
    assert(std::string_view(reinterpret_cast<const char*>(diagnostic.data()+40),size-40)==failure.message);
    rejected([&]{encode_error(std::span(diagnostic).first(40),failure);});
    auto invalid=failure; invalid.message="escape\x1b[2J"; rejected([&]{encode_error(diagnostic,invalid);});
    invalid.message=""; rejected([&]{encode_error(diagnostic,invalid);});
    std::array<char,217> overlong{}; overlong.fill('x'); invalid.message={overlong.data(),overlong.size()};
    rejected([&]{encode_error(diagnostic,invalid);});

    error_info context{error_stage::create,error_domain::worker,1,0,0,"Worker operation failed"};
    const auto ngx=describe_exception("CreateFeature18: 0xbad00002",context);
    assert(ngx.domain==error_domain::ngx && ngx.code==0xbad00002 && ngx.stage==error_stage::create);
    const auto hr=describe_exception("Create texture: 0x8007000E",context);
    assert(hr.domain==error_domain::hresult && hr.code==0x8007000e);
    const auto win32=describe_exception("LoadLibraryEx failed: 0x0000007e",context);
    assert(win32.domain==error_domain::win32 && win32.code==126 && win32.stage==error_stage::load);
    const auto socket=describe_exception("Worker transport failed: 0x00002746",context);
    assert(socket.domain==error_domain::winsock && socket.code==10054);
    const auto readback=describe_exception("Map sequence output: 0x887a0005",context);
    assert(readback.stage==error_stage::readback && readback.code==0x887a0005);
    for (const auto unsafe : {"filesystem error: /private/path/token", "Create texture: 0x8007000G",
         "Create texture: 0x8007000e trailing", "SECRET: 0xbad00002"}) {
        const auto safe=describe_exception(unsafe,context);
        assert(safe.domain==error_domain::worker && safe.code==1 && safe.message==context.message);
    }
    assert(describe_exception("Caller ABI mismatch",context).message.starts_with("Caller ABI mismatch"));
    assert(stage_from_event("evaluate_fence",error_stage::protocol)==error_stage::evaluate_fence);
    assert(stage_from_event("untrusted-stage",error_stage::protocol)==error_stage::protocol);
}
