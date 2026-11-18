use std::path::PathBuf;
use std::process::Command;

fn find_rc_exe() -> Option<PathBuf> {
    let exact = PathBuf::from(r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\rc.exe");
    if exact.exists() {
        return Some(exact);
    }
    let kits_bin = PathBuf::from(r"C:\Program Files (x86)\Windows Kits\10\bin");
    if let Ok(entries) = std::fs::read_dir(&kits_bin) {
        let mut versions: Vec<PathBuf> = entries
            .filter_map(|e| e.ok().map(|x| x.path()))
            .filter(|p| p.join("x64").join("rc.exe").exists())
            .collect();
        versions.sort();
        if let Some(last) = versions.last() {
            return Some(last.join("x64").join("rc.exe"));
        }
    }
    None
}

fn main() {
    println!("cargo:rerun-if-changed=app.rc");
    println!("cargo:rerun-if-changed=app.manifest");
    println!("cargo:rerun-if-changed=app_icon.ico");

    let manifest_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let res_path = manifest_dir.join("target").join("app_resources.res");
    if let Some(parent) = res_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    if let Some(rc) = find_rc_exe() {
        let status = Command::new(rc)
            .args(["/nologo", "/fo"])
            .arg(&res_path)
            .arg("app.rc")
            .status()
            .expect("Failed to execute rc.exe");
        assert!(status.success(), "rc.exe failed to compile app.rc");
    }
}
