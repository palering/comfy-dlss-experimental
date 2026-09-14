#pragma once
// Include before Windows/NGX headers.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <array>
#include <cstdio>
#include <cstring>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>

namespace comfy_dlss {
class worker_connection final {
    SOCKET socket_ = INVALID_SOCKET;
    [[noreturn]] static void socket_failure(const char* operation,int code) {
        char value[16]{};
        std::snprintf(value,sizeof(value),"0x%08x",static_cast<unsigned int>(code));
        throw std::runtime_error(std::string(operation)+": "+value);
    }
    struct startup {
        startup() { WSADATA data{}; if (WSAStartup(MAKEWORD(2,2), &data)) throw std::runtime_error("Worker WSAStartup failed"); }
        ~startup() { WSACleanup(); }
    } startup_;
    void transfer(unsigned char* data, std::size_t size, bool writing, DWORD timeout_ms) {
        const auto deadline=GetTickCount64()+timeout_ms;
        while (size) {
            const auto now=GetTickCount64();
            if (now>=deadline) throw std::runtime_error("Worker transport deadline exceeded");
            const DWORD remaining=static_cast<DWORD>(deadline-now);
            if (setsockopt(socket_,SOL_SOCKET,writing ? SO_SNDTIMEO : SO_RCVTIMEO,reinterpret_cast<const char*>(&remaining),sizeof(remaining)))
                socket_failure("Worker socket timeout setup failed",WSAGetLastError());
            const auto chunk=static_cast<int>(size>1024*1024 ? 1024*1024 : size);
            const auto count=writing ? send(socket_,reinterpret_cast<const char*>(data),chunk,0) : recv(socket_,reinterpret_cast<char*>(data),chunk,0);
            if (count==SOCKET_ERROR) socket_failure("Worker transport failed",WSAGetLastError());
            if (!count) throw std::runtime_error("Worker transport closed");
            data+=count; size-=static_cast<std::size_t>(count);
        }
    }
public:
    worker_connection()=default;
    worker_connection(const worker_connection&)=delete;
    worker_connection& operator=(const worker_connection&)=delete;
    ~worker_connection() { if (socket_!=INVALID_SOCKET) closesocket(socket_); }
    void read(std::span<unsigned char> bytes,DWORD timeout=60000) { transfer(bytes.data(),bytes.size(),false,timeout); }
    void write(std::span<const unsigned char> bytes,DWORD timeout=60000) {
        // send() does not mutate its buffer; common bounded transfer does not
        // dereference the mutable pointer on its write branch.
        transfer(const_cast<unsigned char*>(bytes.data()),bytes.size(),true,timeout);
    }
    void connect_local(unsigned short port,std::wstring_view hex) {
        if (!port || hex.size()!=64 || socket_!=INVALID_SOCKET) throw std::runtime_error("Invalid Worker endpoint");
        std::array<unsigned char,32> token{};
        auto digit=[](wchar_t c)->unsigned int {
            if(c>=L'0' && c<=L'9') return static_cast<unsigned int>(c-L'0');
            if(c>=L'a' && c<=L'f') return static_cast<unsigned int>(c-L'a')+10;
            throw std::runtime_error("Invalid Worker token");
        };
        for(std::size_t i=0;i<token.size();++i) token[i]=static_cast<unsigned char>((digit(hex[2*i])<<4)|digit(hex[2*i+1]));
        socket_=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
        if(socket_==INVALID_SOCKET) socket_failure("Worker socket failed",WSAGetLastError());
        const int no_delay=1;
        if(setsockopt(socket_,IPPROTO_TCP,TCP_NODELAY,reinterpret_cast<const char*>(&no_delay),sizeof(no_delay)))
            socket_failure("Worker TCP_NODELAY failed",WSAGetLastError());
        u_long nonblocking=1;
        if(ioctlsocket(socket_,static_cast<long>(FIONBIO),&nonblocking)) socket_failure("Worker socket mode failed",WSAGetLastError());
        sockaddr_in addr{}; addr.sin_family=AF_INET; addr.sin_port=htons(port); addr.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        if(connect(socket_,reinterpret_cast<const sockaddr*>(&addr),sizeof(addr))) {
            const auto error=WSAGetLastError();
            if(error!=WSAEWOULDBLOCK) socket_failure("Worker connect failed",error);
            fd_set writable,failed; FD_ZERO(&writable); FD_ZERO(&failed); FD_SET(socket_,&writable); FD_SET(socket_,&failed);
            timeval timeout{3,0};
            if(select(0,nullptr,&writable,&failed,&timeout)<=0 || FD_ISSET(socket_,&failed)) throw std::runtime_error("Worker connect timeout");
            int result=0,size=sizeof(result);
            if(getsockopt(socket_,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&result),&size)) socket_failure("Worker connect failed",WSAGetLastError());
            if(result) socket_failure("Worker connect failed",result);
        }
        nonblocking=0;
        if(ioctlsocket(socket_,static_cast<long>(FIONBIO),&nonblocking)) socket_failure("Worker socket mode failed",WSAGetLastError());
        write(token); std::array<unsigned char,1> ack{}; read(ack,3000);
        if(ack[0]!=1) throw std::runtime_error("Worker authentication rejected");
    }
};
}
