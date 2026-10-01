project(
  'hyperlite-cfs',
  'c',
  version: '0.1.0',
  license: 'LicenseRef-PolyForm-Noncommercial-1.0.0',
  meson_version: '>= 1.0',
  default_options: ['c_std=c11', 'warning_level=3', 'werror=true', 'b_ndebug=if-release'],
)

cc = meson.get_compiler('c')
# POSIX and Linux interfaces (strnlen, sigaction, accept4, SO_PEERCRED) on top of C11.
add_project_arguments('-D_GNU_SOURCE', language: 'c')
add_project_arguments(
  cc.get_supported_arguments(
    '-D_FORTIFY_SOURCE=3',
    '-fstack-protector-strong',
    '-Wshadow',
    '-Wstrict-prototypes',
    '-Wmissing-prototypes',
    '-Wformat=2',
    '-Wconversion',
    '-Wno-sign-conversion',
  ),
  language: 'c',
)

# The fuzz build instruments every file for coverage, not only the fuzz target, so libFuzzer can explore the decoder
# and the store.
if get_option('fuzz')
  add_project_arguments('-fsanitize=fuzzer-no-link', language: 'c')
endif

sqlite = dependency('sqlite3')
crypto = dependency('libcrypto')
deps = [sqlite, crypto]

core = static_library(
  'cfscore',
  'src/path.c',
  'src/proto.c',
  'src/store.c',
  'src/locks.c',
  'src/handler.c',
  dependencies: deps,
)

executable(
  'hyperlite-cfs',
  'src/main.c',
  'src/server.c',
  link_with: core,
  dependencies: deps,
  install: true,
  install_dir: get_option('sbindir'),
)

foreach t : ['path', 'proto', 'store', 'locks', 'handler']
  test(
    t,
    executable('test_' + t, 'tests/test_' + t + '.c', link_with: core, dependencies: deps,
               include_directories: include_directories('src')),
  )
endforeach

# Fuzzing the request decoder and the whole handler needs clang's libFuzzer: meson setup -Dfuzz=true with CC=clang.
if get_option('fuzz')
  executable(
    'fuzz_request',
    'fuzz/fuzz_request.c',
    link_with: core,
    dependencies: deps,
    include_directories: include_directories('src'),
    c_args: ['-fsanitize=fuzzer'],
    link_args: ['-fsanitize=fuzzer'],
  )
endif
