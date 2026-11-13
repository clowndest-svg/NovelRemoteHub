package com.daqin.novelremote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.DownloadListener;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

public class MainActivity extends Activity {

    public static final String HOME_URL = "https://scientificexploration.art/novel/";
    public static final String CHECK_UPDATE_URL = "https://scientificexploration.art/novel/api/version";

    private WebView webView;
    private Handler mainHandler = new Handler(Looper.getMainLooper());
    private int myVersionCode = 11;
    private String myVersionName = "1.9.0";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 读取动态包版本信息
        try {
            PackageInfo pInfo = getPackageManager().getPackageInfo(getPackageName(), 0);
            myVersionCode = pInfo.versionCode;
            myVersionName = pInfo.versionName;
        } catch (Exception ignored) {}

        webView = new WebView(this);
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);

        // 注入专属 App 标识到 UserAgent，使前端自动识别 App 环境，隐藏下载按钮并应用原生沉浸样式
        String defaultUA = settings.getUserAgentString();
        settings.setUserAgentString(defaultUA + " NovelRemoteHubApp/" + myVersionName);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                Uri uri = Uri.parse(url);
                if ("https".equals(uri.getScheme()) && "scientificexploration.art".equals(uri.getHost())) {
                    return false;
                }
                if ("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())) {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                }
                return true;
            }
        });

        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String disposition, String mimetype, long length) {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            }
        });
        webView.loadUrl(HOME_URL);

        // 启动时自动检查线上版本
        checkAppUpdate();
    }

    private void checkAppUpdate() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    URL url = new URL(CHECK_UPDATE_URL);
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.setRequestMethod("GET");

                    if (conn.getResponseCode() == 200) {
                        BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
                        StringBuilder sb = new StringBuilder();
                        String line;
                        while ((line = reader.readLine()) != null) {
                            sb.append(line);
                        }
                        reader.close();

                        String jsonStr = sb.toString();
                        final int remoteVersionCode = extractInt(jsonStr, "versionCode", myVersionCode);
                        final String remoteVersionName = extractString(jsonStr, "versionName", myVersionName);
                        final String downloadUrl = extractString(jsonStr, "apkUrl", HOME_URL + "download/NovelRemoteHub.apk");
                        final String changelog = extractString(jsonStr, "changelog", "有最新版本可用");
                        conn.disconnect();

                        // 只有服务端版本严格大于当前安装的本地版本时才提示更新
                        if (remoteVersionCode > myVersionCode) {
                            mainHandler.post(new Runnable() {
                                @Override
                                public void run() {
                                    showUpdateDialog(remoteVersionName, myVersionName, changelog, downloadUrl);
                                }
                            });
                        }
                    }
                } catch (Exception ignored) {}
            }
        }).start();
    }

    private int extractInt(String json, String key, int defVal) {
        try {
            Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*(\\d+)").matcher(json);
            if (m.find()) return Integer.parseInt(m.group(1));
        } catch (Exception e) {}
        return defVal;
    }

    private String extractString(String json, String key, String defVal) {
        try {
            Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*\"([^\"]*)\"").matcher(json);
            if (m.find()) return m.group(1).replace("\\n", "\n");
        } catch (Exception e) {}
        return defVal;
    }

    private void showUpdateDialog(String remoteVersion, String localVersion, String changelog, final String downloadUrl) {
        new AlertDialog.Builder(this)
                .setTitle("🚀 发现新版本 (v" + remoteVersion + ")")
                .setMessage("当前版本: v" + localVersion + "\n最新版本: v" + remoteVersion + "\n\n更新说明：\n" + changelog)
                .setPositiveButton("立即下载更新", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(downloadUrl));
                        startActivity(intent);
                    }
                })
                .setNegativeButton("稍后提醒", null)
                .setCancelable(true)
                .show();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
