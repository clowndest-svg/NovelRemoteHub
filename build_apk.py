# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import shutil
import zipfile

BASE_DIR = r"D:\RuanJian\NovelRemoteHub"
SRC_DIR = os.path.join(BASE_DIR, "app_src")
TOOLS_DIR = os.path.join(BASE_DIR, "apk_tools")
BUILD_DIR = os.path.join(BASE_DIR, "build_tmp")

AAPT2 = os.path.join(TOOLS_DIR, "aapt2.exe")
R8_JAR = os.path.join(TOOLS_DIR, "r8.jar")
ANDROID_JAR = os.path.join(TOOLS_DIR, "android.jar")
UBER_SIGNER = os.path.join(TOOLS_DIR, "uber-apk-signer.jar")

JAVAC = r"E:\BianChengGongJu\GeoServer\OpenJDK17U\bin\javac.exe"
JAVA = r"E:\BianChengGongJu\GeoServer\OpenJDK17U\bin\java.exe"
KEYTOOL = r"E:\BianChengGongJu\GeoServer\OpenJDK17U\bin\keytool.exe"

OUTPUT_APK = os.path.join(BASE_DIR, "DaqinNovelHub.apk")

def run_cmd(cmd, desc=""):
    print(f"--> {desc}...")
    res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if res.returncode != 0:
        print(f"Error in {desc}:")
        print("STDOUT:", res.stdout)
        print("STDERR:", res.stderr)
        raise RuntimeError(f"Command failed: {desc}")
    return res.stdout

def handle_remove_readonly(func, path, exc):
    import stat
    os.chmod(path, stat.S_IWRITE)
    func(path)

def build():
    resolved_build = os.path.realpath(BUILD_DIR)
    if os.path.commonpath((os.path.realpath(BASE_DIR), resolved_build)) != os.path.realpath(BASE_DIR) or resolved_build == os.path.realpath(BASE_DIR):
        raise ValueError("Build directory must remain inside the workspace")
    if os.path.exists(BUILD_DIR):
        try:
            shutil.rmtree(BUILD_DIR, onexc=handle_remove_readonly)
        except Exception:
            try:
                shutil.rmtree(BUILD_DIR, onerror=handle_remove_readonly)
            except Exception:
                pass
    os.makedirs(BUILD_DIR, exist_ok=True)

    res_dir = os.path.join(SRC_DIR, "res", "values")
    os.makedirs(res_dir, exist_ok=True)
    with open(os.path.join(res_dir, "strings.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?><resources><string name="app_name">智笔云枢</string></resources>')

    # 1. aapt2 compile
    compiled_res = os.path.join(BUILD_DIR, "compiled_res.zip")
    run_cmd([AAPT2, "compile", "--dir", os.path.join(SRC_DIR, "res"), "-o", compiled_res], "1. Compiling resources & icons with aapt2")

    # 2. aapt2 link
    unaligned_apk = os.path.join(BUILD_DIR, "unaligned.apk")
    manifest = os.path.join(SRC_DIR, "AndroidManifest.xml")
    run_cmd([
        AAPT2, "link",
        "-I", ANDROID_JAR,
        "--manifest", manifest,
        "-o", unaligned_apk,
        compiled_res
    ], "2. Linking resources and manifest with aapt2")

    # 3. Compile Java with javac
    classes_dir = os.path.join(BUILD_DIR, "classes")
    os.makedirs(classes_dir, exist_ok=True)
    java_file = os.path.join(SRC_DIR, "src", "com", "daqin", "novelremote", "MainActivity.java")
    run_cmd([
        JAVAC,
        "-encoding", "UTF-8",
        "-cp", ANDROID_JAR,
        "-d", classes_dir,
        "-source", "1.8",
        "-target", "1.8",
        java_file
    ], "3. Compiling Java sources")

    # 4. Dex with D8/R8
    class_files = []
    for root, dirs, files in os.walk(classes_dir):
        for file in files:
            if file.endswith(".class"):
                class_files.append(os.path.join(root, file))

    d8_cmd = [
        JAVA,
        "-cp", R8_JAR,
        "com.android.tools.r8.D8",
        "--lib", ANDROID_JAR,
        "--output", BUILD_DIR
    ] + class_files
    run_cmd(d8_cmd, "4. Converting bytecode to classes.dex with D8")

    # 5. Add classes.dex into unaligned.apk
    dex_file = os.path.join(BUILD_DIR, "classes.dex")
    final_unsigned_apk = os.path.join(BUILD_DIR, "final_unsigned.apk")
    shutil.copyfile(unaligned_apk, final_unsigned_apk)
    with zipfile.ZipFile(final_unsigned_apk, 'a') as zf:
        zf.write(dex_file, "classes.dex")
    print("--> 5. Injected classes.dex into APK successfully.")

    # 6. Generate Keystore if needed
    keystore = os.path.join(TOOLS_DIR, "daqin.keystore")
    if not os.path.exists(keystore):
        run_cmd([
            KEYTOOL, "-genkeypair",
            "-v",
            "-keystore", keystore,
            "-alias", "daqin",
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-storepass", "daqin666",
            "-keypass", "daqin666",
            "-dname", "CN=XianQin, OU=Tech, O=Daqin, L=Xianyang, ST=Shaanxi, C=CN"
        ], "6. Generating Android release signing key")

    # 7. Zipalign + v1 + v2 + v3 签名使用 uber-apk-signer
    out_signed_dir = os.path.join(BUILD_DIR, "signed")
    os.makedirs(out_signed_dir, exist_ok=True)
    java_tmp = os.path.join(BUILD_DIR, "java-tmp")
    os.makedirs(java_tmp, exist_ok=True)
    sign_cmd = [
        JAVA,
        "-Djava.io.tmpdir=" + java_tmp,
        "-jar", UBER_SIGNER,
        "-a", final_unsigned_apk,
        "--ks", keystore,
        "--ksPass", "daqin666",
        "--ksKeyPass", "daqin666",
        "--ksAlias", "daqin",
        "-o", out_signed_dir
    ]
    sign_out = run_cmd(sign_cmd, "7. Running Zipalign & v1/v2/v3 Multi-Scheme signing with uber-apk-signer")
    print("Signing Report:\n", sign_out)

    # 找到输出的已签名对齐 APK
    signed_apks = [os.path.join(out_signed_dir, f) for f in os.listdir(out_signed_dir) if f.endswith(".apk")]
    if not signed_apks:
        raise RuntimeError("No signed APK produced by uber-apk-signer!")

    shutil.copyfile(signed_apks[0], OUTPUT_APK)
    universal_apk = os.path.join(BASE_DIR, "NovelRemoteHub.apk")
    shutil.copyfile(signed_apks[0], universal_apk)

    size_mb = os.path.getsize(OUTPUT_APK) / (1024 * 1024)
    print(f"\n🎉 SUCCESS! 【智笔云枢 · NovelRemoteHub】(v1.8.0) 官方正式版 APK 已完成 Zipalign 4字节对齐与 v1/v2/v3 工业级签名！")
    print(f"📦 Output path: {OUTPUT_APK} / {universal_apk} ({size_mb:.2f} MB)")

if __name__ == "__main__":
    build()
