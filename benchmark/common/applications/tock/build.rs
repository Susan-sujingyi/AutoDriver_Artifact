fn main() {
    libtock_build_scripts::auto_layout();
    println!("cargo:rustc-link-arg=-zmax-page-size=4096");
    println!("cargo:rerun-if-env-changed=DRIVERBENCH_PROFILE_RS");
    let profile = std::env::var("DRIVERBENCH_PROFILE_RS").expect("use hardware_benchmark.py build");
    println!("cargo:rerun-if-changed={profile}");
    std::fs::copy(profile, std::path::Path::new(&std::env::var("OUT_DIR").unwrap()).join("profile.rs")).unwrap();
}
