# Sidecar C++ quality rules

Audience: public

The Windows sidecar is a narrow native-code boundary around D3D12 and NGX. It
is compiled as a Windows x86-64 PE with Zig and runs either natively or through
Proton. These rules apply to all sidecar changes.

## ABI and undefined behavior

- Never guess an NGX function declaration. Match function types, structure
  layout, calling convention, enum width, and SDK version to an authoritative
  NVIDIA header before calling an export.
- Resolve optional DLL exports explicitly and treat an absent symbol as a
  capability error. Do not cast arbitrary data pointers to function pointers.
- Keep Windows ABI declarations in one adapter layer. Do not expose SDK-owned
  pointers across the sidecar protocol or persist them between incompatible
  runtime generations.
- Validate dimensions, pitches, formats, offsets, counts, and integer
  conversions before allocating or copying frame data.
- Compile with warnings enabled and keep the build warning-free. Sanitizer
  builds should be added for code paths that can run without proprietary DLLs.

## Ownership and lifetime

- Use RAII for every `HMODULE`, COM interface, Win32 handle, file, allocation,
  D3D12 resource, fence, and NGX feature handle.
- Make owning wrappers non-copyable. Moves must leave the source empty.
- Release NGX features before NGX shutdown, GPU resources before the D3D12
  device, and the NGX module last.
- Never pass borrowed pointers beyond the documented lifetime of their owner.
- A failed operation must leave the worker in a destructible state.
  Exception: after GPU submission, if completion cannot be established, the
  isolated worker must terminate without unwinding in-flight resource owners.
  The current GPU copy layer logs a numeric fence error and calls `std::_Exit(70)`.
  It allocates fence/event/output storage before submission and performs no
  potentially throwing allocations between submission and verified completion.

## Errors and process isolation

- Check every HRESULT, Win32 error, NGX result, file operation, and protocol
  parse. Include the operation and stable numeric code in machine-readable
  errors.
- Do not continue after device removal, ABI mismatch, failed initialization, or
  incomplete GPU synchronization.
- Keep NVIDIA and injection DLLs out of the ComfyUI Python process. A sidecar
  crash must fail one job without corrupting the ComfyUI server.
- Bound input sizes, log sizes, execution time, and paths received from Python.

## Build and verification

- The supported development build uses the repository script and an existing
  Zig installation; do not add a second cross-toolchain without a demonstrated
  blocker.
- Test loader failure, missing exports, invalid inputs, output-file failure, and
  successful Proton/D3D12 initialization independently.
- Keep the executable free of NVIDIA, ReShade, RenoDX, and Proton binaries.
  Those remain user-selected runtime components.

## GPU layout and synchronization references

Row bytes and D3D12-aligned row pitch are distinct; use the returned footprint
and validate every accessed range before copying. All referenced resources,
allocators and command lists must stay alive through verified fence completion.

- [Microsoft: GetCopyableFootprints](https://learn.microsoft.com/en-us/windows/win32/api/d3d12/nf-d3d12-id3d12device-getcopyablefootprints)
- [Microsoft: fence-based resource management](https://learn.microsoft.com/en-us/windows/win32/direct3d12/fence-based-resource-management)
