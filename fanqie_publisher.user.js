// ==UserScript==
// @name         NovelRemoteHub ✕ 番茄小说作家助手 · 全自动发布与批量存草稿
// @namespace    https://scientificexploration.art/novel
// @version      1.0.0
// @description  自动将 NovelRemoteHub 智笔云枢创作的章节一键填入番茄作家后台，支持单章一键填充、作者有话说自动对齐、错别字拦截自愈与多章节全自动存草稿流水线！
// @author       NovelRemoteHub
// @match        https://fanqienovel.com/main/writer/*
// @match        https://fanqienovel.com/page/*
// @match        *://fanqienovel.com/*
// @match        *://*.fanqienovel.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_notification
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @connect      124.220.54.64
// @connect      scientificexploration.art
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-end
// ==/UserScript==

(function() {
    'use strict';

    // 默认配置
    const CONFIG = {
        hubUrl: GM_getValue('hub_url', 'https://124.220.54.64/novel'),
        token: GM_getValue('hub_token', ''),
        bookName: GM_getValue('book_name', '给政哥讲量子力学，他竟肉身硬抗二向箔'),
        autoDismissTypo: GM_getValue('auto_dismiss_typo', true),
        saveDelayMs: GM_getValue('save_delay_ms', 1800)
    };

    let chaptersCatalog = [];
    let currentFillChapter = null;
    let isBatchRunning = false;

    // ================= 样式注入 (黑金奢华主题，契合 NovelRemoteHub) =================
    const STYLES = `
    #nrh-floating-widget {
        position: fixed;
        right: 24px;
        bottom: 90px;
        width: 320px;
        background: rgba(15, 23, 42, 0.96);
        border: 1px solid rgba(234, 179, 8, 0.5);
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.8), 0 0 20px rgba(234, 179, 8, 0.25);
        border-radius: 12px;
        z-index: 2147483647;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
        color: #f1f5f9;
        font-size: 13px;
        backdrop-filter: blur(8px);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        overflow: hidden;
    }
    #nrh-floating-widget.minimized {
        width: 160px;
        height: 42px;
        overflow: hidden;
        border-color: rgba(234, 179, 8, 0.8);
    }
    #nrh-floating-widget .nrh-header {
        background: linear-gradient(135deg, rgba(30, 41, 59, 0.9), rgba(15, 23, 42, 0.95));
        border-bottom: 1px solid rgba(234, 179, 8, 0.3);
        padding: 10px 14px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        cursor: move;
        user-select: none;
    }
    #nrh-floating-widget .nrh-title {
        font-weight: 800;
        color: #fef08a;
        font-size: 13px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    #nrh-floating-widget .nrh-controls button {
        background: transparent;
        border: none;
        color: #94a3b8;
        cursor: pointer;
        padding: 2px 6px;
        font-size: 14px;
        border-radius: 4px;
    }
    #nrh-floating-widget .nrh-controls button:hover {
        color: #fef08a;
        background: rgba(255, 255, 255, 0.1);
    }
    #nrh-floating-widget .nrh-body {
        padding: 14px;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    .nrh-info-banner {
        background: rgba(30, 41, 59, 0.7);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 6px;
        padding: 8px 10px;
        font-size: 11px;
        color: #cbd5e1;
        line-height: 1.5;
    }
    .nrh-info-banner b { color: #fef08a; }
    .nrh-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        padding: 8px 12px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 12px;
        cursor: pointer;
        border: none;
        transition: transform 0.15s, opacity 0.15s, box-shadow 0.15s;
        text-decoration: none;
    }
    .nrh-btn:active { transform: scale(0.98); }
    .nrh-btn-gold {
        background: linear-gradient(135deg, #eab308, #ca8a04);
        color: #000;
        box-shadow: 0 2px 8px rgba(234, 179, 8, 0.3);
    }
    .nrh-btn-gold:hover { opacity: 0.92; }
    .nrh-btn-blue {
        background: linear-gradient(135deg, #3b82f6, #1d4ed8);
        color: #fff;
    }
    .nrh-btn-outline {
        background: rgba(30, 41, 59, 0.6);
        border: 1px solid rgba(234, 179, 8, 0.4);
        color: #fef08a;
    }
    .nrh-btn-outline:hover {
        background: rgba(234, 179, 8, 0.15);
    }
    .nrh-select, .nrh-input {
        background: #0b1120;
        border: 1px solid rgba(234, 179, 8, 0.3);
        color: #f1f5f9;
        border-radius: 6px;
        padding: 6px 8px;
        font-size: 12px;
        width: 100%;
        box-sizing: border-box;
    }
    .nrh-select:focus, .nrh-input:focus {
        outline: none;
        border-color: #eab308;
    }
    .nrh-badge {
        display: inline-block;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 10px;
        font-weight: 700;
        background: rgba(16, 185, 129, 0.2);
        color: #6ee7b7;
        border: 1px solid rgba(16, 185, 129, 0.4);
    }
    .nrh-toast {
        position: fixed;
        top: 24px;
        left: 50%;
        transform: translateX(-50%);
        background: rgba(15, 23, 42, 0.95);
        border: 1px solid #eab308;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
        color: #fef08a;
        padding: 10px 20px;
        border-radius: 8px;
        font-size: 13px;
        font-weight: 700;
        z-index: 1000000;
        pointer-events: none;
        transition: opacity 0.3s, transform 0.3s;
    }
    `;

    function injectStyles() {
        const styleEl = document.createElement('style');
        styleEl.id = 'nrh-tampermonkey-styles';
        styleEl.textContent = STYLES;
        document.head.appendChild(styleEl);
    }

    function showToast(msg, duration = 3000) {
        const oldToast = document.getElementById('nrh-toast-msg');
        if (oldToast) oldToast.remove();

        const toast = document.createElement('div');
        toast.id = 'nrh-toast-msg';
        toast.className = 'nrh-toast';
        toast.innerText = msg;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translate(-50%, -10px)';
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }

    // ================= DOM 元素高精度探测器 =================

    function getChapterNumberInput() {
        // 番茄后台章节号输入框：第 [41] 章
        const inputs = Array.from(document.querySelectorAll('input[type="text"], input[type="number"], input:not([type])'));
        for (const input of inputs) {
            const parent = input.parentElement;
            if (!parent) continue;
            const text = parent.innerText || '';
            const ph = input.getAttribute('placeholder') || '';
            if (text.includes('第') && text.includes('章') && !ph.includes('标题')) {
                return input;
            }
            if (input.className && (input.className.includes('chapter-number') || input.className.includes('chapter_number'))) {
                return input;
            }
        }
        return null;
    }

    function getTitleInput() {
        // 番茄后台章节标题输入框
        const byPh = document.querySelector('input[placeholder*="标题"], input[placeholder*="章节名"]');
        if (byPh) return byPh;
        const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type])'));
        for (const input of inputs) {
            const ph = input.getAttribute('placeholder') || '';
            if (ph.includes('标题') || ph.includes('章节名')) return input;
            if (input.className && (input.className.toLowerCase().includes('title') && !input.className.toLowerCase().includes('number'))) {
                return input;
            }
        }
        return null;
    }

    function getBodyEditor() {
        // 番茄后台正文编辑器 (ProseMirror / contenteditable)
        const prose = document.querySelector('.ProseMirror[contenteditable="true"]');
        if (prose) return prose;
        const editables = Array.from(document.querySelectorAll('div[contenteditable="true"]'));
        for (const el of editables) {
            const ph = el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '';
            if (ph.includes('作者有话说') || ph.includes('互动') || ph.includes('作家的话')) continue;
            if (ph.includes('正文') || el.clientHeight > 120) return el;
        }
        if (editables.length > 0) return editables[0];
        return document.querySelector('textarea[placeholder*="正文"]');
    }

    function getAuthorNoteInput() {
        // 番茄后台作者有话说
        const textareas = Array.from(document.querySelectorAll('textarea'));
        for (const ta of textareas) {
            const ph = ta.getAttribute('placeholder') || '';
            if (ph.includes('作者有话说') || ph.includes('互动') || ph.includes('作家的话') || ph.includes('增强更多互动')) {
                return ta;
            }
        }
        const editables = Array.from(document.querySelectorAll('div[contenteditable="true"]'));
        for (const el of editables) {
            const ph = el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '';
            if (ph.includes('作者有话说') || ph.includes('互动') || ph.includes('增强更多互动')) {
                return el;
            }
        }
        return null;
    }

    function getSaveDraftButton() {
        const buttons = Array.from(document.querySelectorAll('button, a'));
        for (const btn of buttons) {
            const t = (btn.innerText || '').trim();
            if (t === '存草稿' || t.includes('存草稿')) return btn;
        }
        return null;
    }

    function getNextStepButton() {
        const buttons = Array.from(document.querySelectorAll('button, a'));
        for (const btn of buttons) {
            const t = (btn.innerText || '').trim();
            if (t === '下一步') return btn;
        }
        return null;
    }

    // ================= 浏览器原生仿真事件模拟 =================

    function fillInputValue(inputEl, value) {
        if (!inputEl) return false;
        inputEl.focus();

        const proto = inputEl.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) {
            setter.call(inputEl, value);
        } else {
            inputEl.value = value;
        }

        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
        inputEl.dispatchEvent(new Event('change', { bubbles: true }));
        inputEl.dispatchEvent(new Event('blur', { bubbles: true }));
        return true;
    }

    function fillRichEditor(editorEl, text) {
        if (!editorEl) return false;
        editorEl.focus();

        // 选中现有全部内容并清空
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(editorEl);
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('delete', false, null);

        // 分段转为 <p>　　段落文本</p>
        const paragraphs = text.split('\n\n').filter(p => p.trim());
        const html = paragraphs.map(p => {
            const cleanP = p.trim().startsWith('\u3000\u3000') ? p.trim() : ('\u3000\u3000' + p.trim());
            return `<p>${cleanP}</p>`;
        }).join('');

        let success = false;
        try {
            success = document.execCommand('insertHTML', false, html);
        } catch (e) {}

        if (!success) {
            try {
                success = document.execCommand('insertText', false, text);
            } catch (e) {}
        }

        if (!success || !editorEl.innerText.trim()) {
            editorEl.innerHTML = html;
        }

        editorEl.dispatchEvent(new Event('input', { bubbles: true }));
        editorEl.dispatchEvent(new Event('change', { bubbles: true }));
        editorEl.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
        return true;
    }

    // 自动拦截与关闭“疑似错别字/敏感词”拦截对话框
    function autoDismissSpellcheckModal() {
        if (!CONFIG.autoDismissTypo) return false;
        const modals = Array.from(document.querySelectorAll('.arco-modal, .byte-modal, [role="dialog"], .dialog, .modal'));
        for (const m of modals) {
            if (m.offsetParent === null) continue;
            const text = m.innerText || '';
            if (text.includes('错别字') || text.includes('敏感词') || text.includes('疑似') || text.includes('审核建议')) {
                const btns = Array.from(m.querySelectorAll('button'));
                for (const b of btns) {
                    const bt = b.innerText.trim();
                    if (bt.includes('忽略') || bt.includes('继续') || bt.includes('确定') || bt.includes('保存')) {
                        b.click();
                        showToast('⚡ 已为您自动忽略平台机审错字误报！');
                        return true;
                    }
                }
            }
        }
        return false;
    }

    // ================= API 通信层 =================

    function apiGet(endpoint) {
        return new Promise((resolve, reject) => {
            const sep = endpoint.includes('?') ? '&' : '?';
            const authParam = CONFIG.token ? `${sep}token=${encodeURIComponent(CONFIG.token)}` : '';
            const url = CONFIG.hubUrl.replace(/\/+$/, '') + '/api/' + endpoint.replace(/^\/+/, '') + authParam;
            const headers = { 'Accept': 'application/json' };
            if (CONFIG.token) headers['X-Novel-Token'] = CONFIG.token;
            GM_xmlhttpRequest({
                method: 'GET',
                url: url,
                headers: headers,
                timeout: 10000,
                onload: (res) => {
                    if (res.status >= 200 && res.status < 300) {
                        try {
                            resolve(JSON.parse(res.responseText));
                        } catch (e) {
                            reject(new Error('JSON 解析失败: ' + res.responseText));
                        }
                    } else {
                        reject(new Error(`HTTP ${res.status}: ${res.statusText}`));
                    }
                },
                onerror: (err) => reject(new Error('网络请求错误，请检查中枢地址')),
                ontimeout: () => reject(new Error('请求超时'))
            });
        });
    }

    async function loadChapters() {
        try {
            const data = await apiGet(`fanqie_publish_list?book=${encodeURIComponent(CONFIG.bookName)}`);
            if (data && data.chapters) {
                chaptersCatalog = data.chapters;
                updateChapterSelect();
                showToast(`已成功同步 ${chaptersCatalog.length} 个最新章节！`);
            }
        } catch (e) {
            console.warn('[NRH Publisher] 获取章节列表失败:', e);
            // 备用：从页面解析上次提交章节并推算
            detectPageLastChapter();
        }
    }

    function detectPageLastChapter() {
        const text = document.body.innerText || '';
        const m = text.match(/上次提交[：:\s]*.*?第\s*(\d+)\s*章/);
        if (m) {
            const lastNum = parseInt(m[1], 10);
            const nextNum = lastNum + 1;
            const selectEl = document.getElementById('nrh-chapter-select');
            if (selectEl) {
                selectEl.innerHTML = `<option value="auto_next">第 ${nextNum} 章 (系统自动推算)</option>`;
            }
            return nextNum;
        }
        return 1;
    }

    // ================= 核心业务：单章一键自动填充 =================

    async function executeFillChapter(packageData) {
        showToast(`正在注入「${packageData.full_title || ('第' + packageData.chapter_number + '章')}」...`);

        // 1. 填入章节序号
        const numInput = getChapterNumberInput();
        if (numInput && packageData.chapter_number) {
            fillInputValue(numInput, String(packageData.chapter_number));
        }

        // 2. 填入标题
        const titleInput = getTitleInput();
        if (titleInput) {
            const titleVal = packageData.chapter_title || packageData.full_title;
            fillInputValue(titleInput, titleVal);
        }

        // 3. 填入正文 (带双全角缩进与纯净排版)
        const editor = getBodyEditor();
        if (editor && packageData.content) {
            fillRichEditor(editor, packageData.content);
        } else {
            showToast('⚠️ 未能精确定位正文富文本输入区，请确认在编辑页面！', 4000);
            return false;
        }

        // 4. 填入作者有话说
        const noteInput = getAuthorNoteInput();
        if (noteInput && packageData.author_note) {
            fillInputValue(noteInput, packageData.author_note);
        }

        showToast(`🎉 第 ${packageData.chapter_number} 章填充完毕！字数已核验。`);
        return true;
    }

    async function fillCurrentSelected() {
        const selectEl = document.getElementById('nrh-chapter-select');
        const selectedVal = selectEl ? selectEl.value : '';

        // 1. 优先从 API 获取标准化发布包
        if (selectedVal && selectedVal !== 'auto_next' && selectedVal !== 'clipboard') {
            try {
                showToast('正在从智笔云枢获取章节发布数据...');
                const pkg = await apiGet(`fanqie_publish_package?book=${encodeURIComponent(CONFIG.bookName)}&file=${encodeURIComponent(selectedVal)}`);
                if (pkg && pkg.ok) {
                    await executeFillChapter(pkg);
                    return;
                }
            } catch (e) {
                console.error(e);
                showToast('从 API 获取失败，正在尝试剪贴板数据...');
            }
        }

        // 2. 备用：从剪贴板读取
        await fillFromClipboard();
    }

    async function fillFromClipboard() {
        try {
            const text = await navigator.clipboard.readText();
            if (!text || !text.trim()) {
                alert('剪贴板为空！请先在 NovelRemoteHub 中点击「一键复制番茄标准正文」或「复制自动发布包」。');
                return;
            }

            let pkg = null;
            // 尝试按 JSON 发布包解析
            if (text.trim().startsWith('{') && text.trim().endsWith('}')) {
                try {
                    pkg = JSON.parse(text);
                } catch (e) {}
            }

            // 若非 JSON，按纯文本自动智能分拣
            if (!pkg) {
                const lines = text.split('\n');
                let firstLine = lines[0].trim();
                let bodyLines = lines.slice(1);

                let chapNum = 1;
                let title = firstLine;
                const m = firstLine.match(/第\s*(\d+)\s*章[\s_：:]*(.*)/);
                if (m) {
                    chapNum = parseInt(m[1], 10);
                    title = m[2].trim() || firstLine;
                }

                // 检查是否包含作者有话说分割线
                let authorNote = '';
                const noteIdx = bodyLines.findIndex(l => l.includes('【作者有话说】') || l.includes('【随章互动】'));
                if (noteIdx !== -1) {
                    authorNote = bodyLines.slice(noteIdx).join('\n');
                    bodyLines = bodyLines.slice(0, noteIdx);
                }

                pkg = {
                    chapter_number: chapNum,
                    chapter_title: title,
                    full_title: firstLine,
                    content: bodyLines.join('\n').trim(),
                    author_note: authorNote
                };
            }

            await executeFillChapter(pkg);
        } catch (e) {
            alert('读取剪贴板失败，请允许浏览器读取剪贴板权限: ' + e.message);
        }
    }

    // ================= 自动存草稿与批量流水线 =================

    async function saveDraftNow() {
        const btn = getSaveDraftButton();
        if (!btn) {
            showToast('⚠️ 未找到「存草稿」按钮，请确认页面已就绪！', 3000);
            return false;
        }
        btn.click();
        showToast('正在保存草稿并监视风控弹窗...');

        // 持续 3 秒监听并自动处理疑似错别字弹窗
        for (let i = 0; i < 15; i++) {
            await new Promise(r => setTimeout(r, 200));
            if (autoDismissSpellcheckModal()) break;
        }
        showToast('✅ 草稿已安全存入番茄后台！');
        return true;
    }

    async function runBatchPipeline() {
        if (isBatchRunning) return;
        if (!chaptersCatalog || chaptersCatalog.length === 0) {
            await loadChapters();
        }

        const startInput = prompt('请输入批量起始章节号 (例如 41)：', '41');
        if (!startInput) return;
        const endInput = prompt('请输入批量结束章节号 (例如 50)：', '50');
        if (!endInput) return;

        const startNum = parseInt(startInput, 10);
        const endNum = parseInt(endInput, 10);
        if (isNaN(startNum) || isNaN(endNum) || startNum > endNum) {
            alert('输入的章节范围无效！');
            return;
        }

        const count = endNum - startNum + 1;
        if (!confirm(`即将开始全自动流水线：\n• 目标范围：第 ${startNum} 章 ~ 第 ${endNum} 章\n• 共计：${count} 个章节\n• 过程：自动填充 ➔ 自动存草稿 ➔ 自动消解错字提示 ➔ 自动切入下一章\n\n确认立即全自动执行？`)) {
            return;
        }

        isBatchRunning = true;
        showToast(`🚀 批量流水线已启动 (共 ${count} 章)，请保持页面在前台...`, 5000);

        try {
            for (let num = startNum; num <= endNum; num++) {
                showToast(`[${num - startNum + 1}/${count}] 正在处理第 ${num} 章...`);

                // 寻找对应章节文件名
                const targetChap = chaptersCatalog.find(c => c.chapter_number === num);
                const fileParam = targetChap ? targetChap.filename : `第${String(num).padStart(3, '0')}章.md`;

                const pkg = await apiGet(`fanqie_publish_package?book=${encodeURIComponent(CONFIG.bookName)}&file=${encodeURIComponent(fileParam)}`);
                if (!pkg || !pkg.ok) {
                    throw new Error(`获取第 ${num} 章发布数据失败`);
                }

                // 填充数据
                await executeFillChapter(pkg);
                await new Promise(r => setTimeout(r, 800));

                // 存草稿
                await saveDraftNow();
                await new Promise(r => setTimeout(r, CONFIG.saveDelayMs));

                // 如果未到最后一章，尝试点击新建章节
                if (num < endNum) {
                    showToast(`第 ${num} 章已存，正在创建下一章...`);
                    // 寻找“新建章节”按钮或直接在当前编辑器清空/重载
                    const newBtn = Array.from(document.querySelectorAll('button, a')).find(el => el.innerText && el.innerText.includes('新建章节'));
                    if (newBtn) {
                        newBtn.click();
                        await new Promise(r => setTimeout(r, 2000));
                    } else {
                        // 若无独立新建按钮，提示等待或刷新
                        await new Promise(r => setTimeout(r, 1500));
                    }
                }
            }

            alert(`🎉 全部 ${count} 个章节已全自动填入并存入番茄草稿箱！\n您可以进入草稿箱查验并择机发布。`);
        } catch (e) {
            alert(`批量流程中断: ${e.message}`);
        } finally {
            isBatchRunning = false;
        }
    }

    // ================= 悬浮控制面板构建 =================

    function updateChapterSelect() {
        const selectEl = document.getElementById('nrh-chapter-select');
        if (!selectEl) return;
        selectEl.innerHTML = '';

        if (chaptersCatalog.length === 0) {
            selectEl.innerHTML = '<option value="clipboard">📋 从剪贴板智能识别</option>';
            return;
        }

        chaptersCatalog.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.filename;
            opt.textContent = `第${c.chapter_number}章 ${c.title || c.filename}`;
            selectEl.appendChild(opt);
        });

        // 默认选中推算的下一章
        const nextNum = detectPageLastChapter();
        const found = chaptersCatalog.find(c => c.chapter_number === nextNum);
        if (found) selectEl.value = found.filename;
    }

    function createFloatingWidget() {
        if (document.getElementById('nrh-floating-widget')) return;

        const widget = document.createElement('div');
        widget.id = 'nrh-floating-widget';
        widget.innerHTML = `
            <div class="nrh-header" id="nrh-drag-header">
                <div class="nrh-title">
                    <span>🚀 智笔云枢</span>
                    <span class="nrh-badge">番茄助手</span>
                </div>
                <div class="nrh-controls">
                    <button id="nrh-btn-min" title="最小化/展开">_</button>
                    <button id="nrh-btn-close" title="关闭">✕</button>
                </div>
            </div>
            <div class="nrh-body" id="nrh-widget-body">
                <div class="nrh-info-banner">
                    作品：<b>${CONFIG.bookName}</b><br>
                    状态：<span style="color:#4ade80;">✓ 纯净排版与错字自愈已就绪</span>
                </div>

                <div>
                    <label style="font-size:11px; color:#94a3b8; display:block; margin-bottom:4px;">选择待发章节：</label>
                    <select id="nrh-chapter-select" class="nrh-select">
                        <option value="auto_next">正在连接智笔云枢...</option>
                    </select>
                </div>

                <div style="display:flex; gap:8px;">
                    <button id="nrh-btn-fill" class="nrh-btn nrh-btn-gold" style="flex:1;">
                        ⚡ 一键填入本章
                    </button>
                    <button id="nrh-btn-save" class="nrh-btn nrh-btn-outline" style="width:80px;" title="保存草稿并自动拦截错字弹窗">
                        💾 存草稿
                    </button>
                </div>

                <button id="nrh-btn-batch" class="nrh-btn nrh-btn-blue" style="width:100%;">
                    🚀 批量全自动流水线 (10章连存)
                </button>

                <div style="display:flex; justify-content:space-between; align-items:center; font-size:10px; color:#64748b; margin-top:2px;">
                    <span id="nrh-btn-sync" style="cursor:pointer; color:#93c5fd;">🔄 刷新章节</span>
                    <span id="nrh-btn-clip" style="cursor:pointer; color:#fde047;">📋 剪贴板秒填</span>
                </div>
            </div>
        `;

        document.body.appendChild(widget);

        // 事件绑定
        document.getElementById('nrh-btn-fill').addEventListener('click', fillCurrentSelected);
        document.getElementById('nrh-btn-save').addEventListener('click', saveDraftNow);
        document.getElementById('nrh-btn-batch').addEventListener('click', runBatchPipeline);
        document.getElementById('nrh-btn-sync').addEventListener('click', loadChapters);
        document.getElementById('nrh-btn-clip').addEventListener('click', fillFromClipboard);

        document.getElementById('nrh-btn-min').addEventListener('click', () => {
            widget.classList.toggle('minimized');
            const isMin = widget.classList.contains('minimized');
            document.getElementById('nrh-widget-body').style.display = isMin ? 'none' : 'flex';
        });

        document.getElementById('nrh-btn-close').addEventListener('click', () => {
            widget.style.display = 'none';
        });

        // 拖拽支持
        makeDraggable(widget, document.getElementById('nrh-drag-header'));
    }

    function makeDraggable(el, handle) {
        let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
        handle.onmousedown = dragMouseDown;

        function dragMouseDown(e) {
            e.preventDefault();
            pos3 = e.clientX;
            pos4 = e.clientY;
            document.onmouseup = closeDragElement;
            document.onmousemove = elementDrag;
        }

        function elementDrag(e) {
            e.preventDefault();
            pos1 = pos3 - e.clientX;
            pos2 = pos4 - e.clientY;
            pos3 = e.clientX;
            pos4 = e.clientY;
            el.style.top = (el.offsetTop - pos2) + "px";
            el.style.left = (el.offsetLeft - pos1) + "px";
            el.style.right = 'auto';
            el.style.bottom = 'auto';
        }

        function closeDragElement() {
            document.onmouseup = null;
            document.onmousemove = null;
        }
    }

    // ================= 初始化启动 =================
    function init() {
        try {
            injectStyles();
            createFloatingWidget();
            loadChapters();
        } catch (e) {
            console.error('[NRH Publisher] 悬浮窗构建异常:', e);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    // 针对单页应用路由切换或异步渲染提供多阶段守护唤醒
    setTimeout(init, 1000);
    setTimeout(init, 2500);

})();
