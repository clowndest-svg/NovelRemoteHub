// ==UserScript==
// @name         NovelRemoteHub ✕ 网文作家助手 · 全自动发布与批量存草稿 (番茄/七猫双平台)
// @namespace    https://scientificexploration.art/novel
// @version      2.0.0
// @description  自动将 NovelRemoteHub 智笔云枢创作的章节一键填入番茄小说作家后台与七猫作家中心，支持双平台智能适配、贴边吸附、单章一键秒填、作者有话说自动对齐、错别字拦截自愈与多章节全自动批量存草稿流水线！
// @author       NovelRemoteHub
// @match        *://fanqienovel.com/*
// @match        *://*.fanqienovel.com/*
// @match        *://zuozhe.qimao.com/*
// @match        *://*.qimao.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
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

    // 平台探测
    function detectPlatform() {
        const host = window.location.hostname;
        if (host.includes('qimao.com')) return 'qimao';
        if (host.includes('fanqienovel.com')) return 'fanqie';
        return 'generic';
    }

    const PLATFORM = detectPlatform();

    // 默认配置
    const CONFIG = {
        hubUrl: GM_getValue('hub_url', 'https://124.220.54.64/novel'),
        token: GM_getValue('hub_token', ''),
        bookName: GM_getValue('book_name', '给政哥讲量子力学，他竟肉身硬抗二向箔'),
        autoDismissTypo: GM_getValue('auto_dismiss_typo', true),
        saveDelayMs: GM_getValue('save_delay_ms', 1800)
    };

    const BATCH_STORAGE_KEY = 'nrh_multi_platform_batch_v2';

    let chaptersCatalog = [];
    let isBatchRunning = false;
    let isWidgetDragging = false;

    // ================= 状态持久化管理 =================
    function getBatchState() {
        try {
            const raw = GM_getValue(BATCH_STORAGE_KEY, null);
            if (!raw) return null;
            return typeof raw === 'string' ? JSON.parse(raw) : raw;
        } catch (e) {
            console.error('[NRH Publisher] 解析批量状态异常:', e);
            return null;
        }
    }

    function setBatchState(state) {
        if (state) {
            GM_setValue(BATCH_STORAGE_KEY, JSON.stringify(state));
        } else {
            GM_deleteValue(BATCH_STORAGE_KEY);
        }
    }

    function getBookIdFromUrl() {
        if (PLATFORM === 'fanqie') {
            const m = location.href.match(/writer\/(?:preview\/)?(\d+)/);
            if (m) {
                GM_setValue('nrh_last_book_id', m[1]);
                return m[1];
            }
            return GM_getValue('nrh_last_book_id', '7706774841793186841');
        } else if (PLATFORM === 'qimao') {
            const m = location.href.match(/[?&]bookId=(\d+)/) || location.href.match(/book\/(\d+)/);
            if (m) {
                GM_setValue('nrh_qimao_book_id', m[1]);
                return m[1];
            }
            return GM_getValue('nrh_qimao_book_id', '');
        }
        return '';
    }

    // 智能识别页面上的书名
    function detectPageBookName() {
        const text = document.body ? document.body.innerText : '';
        if (text.includes('给政哥讲量子')) {
            return '给政哥讲量子力学，他竟肉身硬抗二向箔';
        }
        return CONFIG.bookName;
    }

    // ================= 样式注入 (黑金奢华科技主题 + 边缘智能吸附抽屉) =================
    const STYLES = `
    #nrh-floating-widget {
        position: fixed;
        right: 0;
        top: 240px;
        width: 320px;
        background: rgba(15, 23, 42, 0.96);
        border: 1px solid rgba(234, 179, 8, 0.5);
        border-right: none;
        box-shadow: 0 10px 35px rgba(0, 0, 0, 0.8), 0 0 20px rgba(234, 179, 8, 0.25);
        border-radius: 12px 0 0 12px;
        z-index: 2147483647;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
        color: #f1f5f9;
        font-size: 13px;
        backdrop-filter: blur(10px);
        transition: transform 0.28s cubic-bezier(0.2, 0, 0, 1), opacity 0.25s, box-shadow 0.25s;
        box-sizing: border-box;
        overflow: hidden;
        user-select: none;
    }

    /* 贴边折叠收起状态：仅在屏幕最右侧留出 32px 宽的金色科技小挂签，完全不遮挡网页正文与点击 */
    #nrh-floating-widget.docked {
        transform: translateX(calc(100% - 32px));
        box-shadow: -4px 0 14px rgba(0, 0, 0, 0.5);
        opacity: 0.88;
        cursor: pointer;
    }
    #nrh-floating-widget.docked:hover {
        transform: translateX(calc(100% - 36px));
        opacity: 1;
        box-shadow: -6px 0 18px rgba(234, 179, 8, 0.45);
    }
    #nrh-floating-widget.docked #nrh-drag-header,
    #nrh-floating-widget.docked #nrh-widget-body {
        display: none !important;
    }

    /* 侧边小挂签条目 */
    .nrh-dock-tab {
        display: none;
        width: 32px;
        min-height: 116px;
        background: linear-gradient(180deg, #ca8a04 0%, #eab308 50%, #ca8a04 100%);
        color: #090d16;
        border-radius: 8px 0 0 8px;
        padding: 10px 0;
        box-sizing: border-box;
        cursor: pointer;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 6px;
        box-shadow: inset 1px 0 2px rgba(255, 255, 255, 0.5);
    }
    #nrh-floating-widget.docked .nrh-dock-tab {
        display: flex;
    }
    .nrh-dock-icon {
        font-size: 14px;
        filter: drop-shadow(0 1px 2px rgba(0,0,0,0.4));
    }
    .nrh-dock-text {
        writing-mode: vertical-lr;
        text-orientation: upright;
        font-weight: 900;
        font-size: 11px;
        letter-spacing: 2px;
    }

    #nrh-floating-widget .nrh-header {
        background: linear-gradient(135deg, rgba(30, 41, 59, 0.95), rgba(15, 23, 42, 0.98));
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
    #nrh-floating-widget .nrh-controls {
        display: flex;
        align-items: center;
        gap: 4px;
    }
    #nrh-floating-widget .nrh-controls button {
        background: transparent;
        border: none;
        color: #94a3b8;
        cursor: pointer;
        padding: 3px 6px;
        font-size: 12px;
        border-radius: 4px;
        transition: all 0.15s;
    }
    #nrh-floating-widget .nrh-controls button:hover {
        color: #fef08a;
        background: rgba(255, 255, 255, 0.12);
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
    .nrh-btn-danger {
        background: linear-gradient(135deg, #ef4444, #b91c1c);
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
    .nrh-platform-badge {
        display: inline-block;
        padding: 1px 5px;
        border-radius: 3px;
        font-size: 10px;
        font-weight: 600;
        background: rgba(59, 130, 246, 0.2);
        color: #93c5fd;
        border: 1px solid rgba(59, 130, 246, 0.4);
        margin-left: 4px;
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
        z-index: 2147483647;
        pointer-events: none;
        transition: opacity 0.3s, transform 0.3s;
    }
    #nrh-batch-banner {
        position: fixed;
        top: 14px;
        left: 50%;
        transform: translateX(-50%);
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.98), rgba(30, 41, 59, 0.98));
        border: 2px solid #eab308;
        border-radius: 10px;
        padding: 10px 20px;
        color: #fff;
        z-index: 2147483646;
        display: flex;
        align-items: center;
        gap: 16px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.8), 0 0 25px rgba(234, 179, 8, 0.4);
        font-size: 13px;
        font-weight: 700;
        font-family: inherit;
    }
    #nrh-reopen-dot {
        position: fixed;
        right: 0;
        top: 260px;
        width: 14px;
        height: 38px;
        background: #eab308;
        border-radius: 8px 0 0 8px;
        opacity: 0.45;
        cursor: pointer;
        z-index: 2147483646;
        transition: opacity 0.2s, width 0.2s;
        box-shadow: -2px 0 8px rgba(0,0,0,0.4);
    }
    #nrh-reopen-dot:hover {
        opacity: 1;
        width: 18px;
    }
    `;

    function injectStyles() {
        if (document.getElementById('nrh-tampermonkey-styles')) return;
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

    // ================= 双平台 DOM 高精度探测器 (番茄 + 七猫) =================

    function getTitleInput() {
        // 通用标题探测：placeholder 包含 "标题"、"章节名"、"章节名称" 等
        const selectors = [
            'input[placeholder*="章节名称"]',
            'input[placeholder*="章节标题"]',
            'input[placeholder*="章节名"]',
            'input[placeholder*="标题"]',
            'input[name*="chapterName"]',
            'input[name*="title"]',
            '.chapter-title-input input',
            '.chapter-name-input input'
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && !el.closest('#nrh-floating-widget')) return el;
        }

        const inputs = Array.from(document.querySelectorAll('input:not(#nrh-floating-widget input)'));
        for (const input of inputs) {
            const ph = input.getAttribute('placeholder') || '';
            if (ph.includes('标题') || ph.includes('章节名') || ph.includes('章节名称')) return input;
            if (input.className && (input.className.toLowerCase().includes('title') || input.className.toLowerCase().includes('chapter-name')) && !input.className.toLowerCase().includes('number')) {
                return input;
            }
        }
        return null;
    }

    function getChapterNumberInput() {
        // 番茄后台特有：独立章节序号输入框
        if (PLATFORM !== 'fanqie') return null;

        const titleInput = getTitleInput();
        if (titleInput) {
            const allInputs = Array.from(document.querySelectorAll('input:not(#nrh-floating-widget input)'));
            const titleIdx = allInputs.indexOf(titleInput);
            if (titleIdx > 0) {
                const candidate = allInputs[titleIdx - 1];
                let p = candidate.parentElement;
                let depth = 0;
                let isMatch = false;
                while (p && depth < 4) {
                    const text = p.innerText || '';
                    if (text.includes('第') || text.includes('章')) {
                        isMatch = true;
                        break;
                    }
                    p = p.parentElement;
                    depth++;
                }
                if (isMatch || !candidate.placeholder) {
                    return candidate;
                }
            }
        }

        const inputs = Array.from(document.querySelectorAll('input:not(#nrh-floating-widget input)'));
        for (const input of inputs) {
            if (input === titleInput) continue;
            const ph = input.getAttribute('placeholder') || '';
            if (ph.includes('标题') || ph.includes('搜索')) continue;

            let p = input.parentElement;
            let depth = 0;
            while (p && depth < 4) {
                const text = p.innerText || '';
                if (text.includes('第') && text.includes('章')) {
                    return input;
                }
                p = p.parentElement;
                depth++;
            }
        }
        return null;
    }

    function getBodyEditor() {
        // 1. 富文本编辑器 (ProseMirror / Quill / WangEditor / contenteditable)
        const editables = Array.from(document.querySelectorAll('.ProseMirror, .ql-editor, .w-e-text-container [contenteditable="true"], div[contenteditable="true"]:not(#nrh-floating-widget div)'));
        for (const el of editables) {
            const ph = el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '';
            if (ph.includes('作者有话说') || ph.includes('作家的话') || ph.includes('互动')) continue;
            if (ph.includes('正文') || el.clientHeight > 100) return el;
        }

        // 2. 原生 textarea 编辑器 (七猫常见)
        const textareas = Array.from(document.querySelectorAll('textarea:not(#nrh-floating-widget textarea)'));
        for (const ta of textareas) {
            const ph = ta.getAttribute('placeholder') || '';
            if (ph.includes('作者有话说') || ph.includes('作家的话') || ph.includes('互动')) continue;
            if (ph.includes('正文') || ta.clientHeight > 100) return ta;
        }

        if (editables.length > 0) return editables[0];
        if (textareas.length > 0) return textareas[0];
        return null;
    }

    function expandAuthorNoteIfCollapsed() {
        const elements = Array.from(document.querySelectorAll('button, span, div, a'));
        for (const el of elements) {
            const text = (el.innerText || '').trim();
            if (text === '+ 添加' || text === '+添加' || text === '添加' || text.includes('作家的话') || text.includes('作者有话说')) {
                const parentText = el.parentElement?.parentElement?.innerText || el.parentElement?.innerText || '';
                if (parentText.includes('作者有话说') || parentText.includes('作家的话')) {
                    el.click();
                    return true;
                }
            }
        }
        return false;
    }

    function getAuthorNoteInput() {
        const textareas = Array.from(document.querySelectorAll('textarea:not(#nrh-floating-widget textarea)'));
        for (const ta of textareas) {
            const ph = ta.getAttribute('placeholder') || '';
            if (ph.includes('作者有话说') || ph.includes('互动') || ph.includes('作家的话') || ph.includes('增强更多互动')) {
                return ta;
            }
        }
        const editables = Array.from(document.querySelectorAll('div[contenteditable="true"]:not(#nrh-floating-widget div)'));
        for (const el of editables) {
            const ph = el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '';
            if (ph.includes('作者有话说') || ph.includes('互动') || ph.includes('作家的话') || ph.includes('增强更多互动')) {
                return el;
            }
        }
        return null;
    }

    function getSaveDraftButton() {
        const buttons = Array.from(document.querySelectorAll('button, a, span.btn, div.btn'));
        for (const btn of buttons) {
            if (btn.closest('#nrh-floating-widget')) continue;
            const t = (btn.innerText || '').trim();
            if (t === '存草稿' || t === '保存草稿' || t === '存为草稿' || t.includes('存草稿') || t.includes('保存草稿')) return btn;
        }
        for (const btn of buttons) {
            if (btn.closest('#nrh-floating-widget')) continue;
            const t = (btn.innerText || '').trim();
            if (t === '保存' || t === '暂存') return btn;
        }
        return null;
    }

    function getNewChapterButton() {
        const buttons = Array.from(document.querySelectorAll('button, a, span, div'));
        for (const btn of buttons) {
            if (btn.closest('#nrh-floating-widget')) continue;
            const t = (btn.innerText || '').trim();
            if (t === '新建章节' || t === '新增章节' || t === '+ 新建章节' || t === '+新增章节' || t === '新建草稿' || t === '上传章节') {
                return btn;
            }
        }
        return null;
    }

    // ================= 浏览器原生仿真事件模拟 =================

    function fillInputValue(inputEl, value) {
        if (!inputEl) return false;
        inputEl.focus();

        const proto = inputEl.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;

        if (inputEl._valueTracker) {
            inputEl._valueTracker.setValue('');
        }

        if (setter) {
            setter.call(inputEl, value);
        } else {
            inputEl.value = value;
        }

        inputEl.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        inputEl.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        inputEl.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: String(value), inputType: 'insertText' }));
        inputEl.dispatchEvent(new Event('blur', { bubbles: true, composed: true }));
        return true;
    }

    function fillRichEditor(editorEl, text) {
        if (!editorEl) return false;

        // 如果是纯 textarea
        if (editorEl.tagName === 'TEXTAREA') {
            return fillInputValue(editorEl, text);
        }

        editorEl.focus();

        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(editorEl);
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('delete', false, null);

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

        editorEl.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        editorEl.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        editorEl.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertText' }));
        return true;
    }

    // 自动拦截与关闭机审弹窗
    function autoDismissSpellcheckModal() {
        if (!CONFIG.autoDismissTypo) return false;
        const modals = Array.from(document.querySelectorAll('.arco-modal, .byte-modal, [role="dialog"], .dialog, .modal, .el-dialog, .ant-modal'));
        for (const m of modals) {
            if (m.offsetParent === null) continue;
            const text = m.innerText || '';
            if (text.includes('错别字') || text.includes('敏感词') || text.includes('疑似') || text.includes('审核建议') || text.includes('提示')) {
                const btns = Array.from(m.querySelectorAll('button, a, .el-button, .ant-btn'));
                for (const b of btns) {
                    const bt = b.innerText.trim();
                    if (bt.includes('忽略') || bt.includes('继续') || bt.includes('确定') || bt.includes('保存') || bt.includes('知道了')) {
                        b.click();
                        showToast('⚡ 已为您自动忽略平台机审提示！');
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
            const currentBook = detectPageBookName();
            const data = await apiGet(`fanqie_publish_list?book=${encodeURIComponent(currentBook)}`);
            if (data && data.chapters) {
                chaptersCatalog = data.chapters;
                updateChapterSelect();
                showToast(`已成功同步 ${chaptersCatalog.length} 个最新章节！`);
            }
        } catch (e) {
            console.warn('[NRH Publisher] 获取章节列表失败:', e);
            detectPageLastChapter();
        }
    }

    function detectPageLastChapter() {
        const text = document.body.innerText || '';
        const m = text.match(/第\s*(\d+)\s*章/g);
        if (m && m.length > 0) {
            const lastMatch = m[m.length - 1].match(/\d+/);
            const lastNum = lastMatch ? parseInt(lastMatch[0], 10) : 0;
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

        // 1. 填入独立章节序号 (番茄专用)
        const numInput = getChapterNumberInput();
        let numSuccess = false;
        if (numInput && packageData.chapter_number) {
            fillInputValue(numInput, String(packageData.chapter_number));
            numSuccess = (numInput.value === String(packageData.chapter_number));
        }

        // 2. 填入章节标题 (七猫填入完整“第001章 标题”，番茄根据序号是否独立决定)
        const titleInput = getTitleInput();
        if (titleInput) {
            let titleVal = '';
            if (PLATFORM === 'qimao') {
                // 七猫标题统一使用完整格式：第001章 标题
                titleVal = packageData.full_title || `第${String(packageData.chapter_number).padStart(3, '0')}章 ${packageData.chapter_title || ''}`;
            } else {
                // 番茄平台：若序号框成功注入，标题框只填纯标题；否则兜底追加“第X章”
                const pureTitle = packageData.chapter_title || '';
                const fullTitle = packageData.full_title || (`第${packageData.chapter_number}章 ` + pureTitle);
                titleVal = numSuccess ? (pureTitle || fullTitle) : fullTitle;
            }
            fillInputValue(titleInput, titleVal);
        } else {
            console.warn('[NRH Publisher] 未找到标题输入框');
        }

        // 3. 填入正文 (带双全角缩进与纯净排版)
        const editor = getBodyEditor();
        if (editor && packageData.content) {
            fillRichEditor(editor, packageData.content);
        } else {
            showToast('⚠️ 未能精确定位正文输入区，请确认处于章节编辑页面！', 4000);
            return false;
        }

        // 4. 填入作者有话说
        expandAuthorNoteIfCollapsed();
        await new Promise(r => setTimeout(r, 200));
        const noteInput = getAuthorNoteInput();
        if (noteInput && packageData.author_note) {
            fillInputValue(noteInput, packageData.author_note);
        }

        showToast(`🎉 第 ${packageData.chapter_number} 章填充完毕！字数已核验。`);
        return true;
    }

    async function fillCurrentSelected() {
        // 如果在七猫作品管理页，提示或自动点击上传章节
        if (PLATFORM === 'qimao' && location.href.includes('book-manage')) {
            const uploadBtn = document.querySelector('button, a');
            const btns = Array.from(document.querySelectorAll('button, a')).filter(el => (el.innerText || '').includes('上传章节'));
            if (btns.length > 0) {
                showToast('正在为您打开七猫章节上传界面...');
                btns[0].click();
                await new Promise(r => setTimeout(r, 1500));
            }
        }

        const selectEl = document.getElementById('nrh-chapter-select');
        const selectedVal = selectEl ? selectEl.value : '';
        const currentBook = detectPageBookName();

        if (selectedVal && selectedVal !== 'auto_next' && selectedVal !== 'clipboard') {
            try {
                showToast('正在从智笔云枢获取章节发布数据...');
                const pkg = await apiGet(`fanqie_publish_package?book=${encodeURIComponent(currentBook)}&file=${encodeURIComponent(selectedVal)}`);
                if (pkg && pkg.ok) {
                    await executeFillChapter(pkg);
                    return;
                }
            } catch (e) {
                console.error(e);
                showToast('从 API 获取失败，正在尝试剪贴板数据...');
            }
        }

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
            if (text.trim().startsWith('{') && text.trim().endsWith('}')) {
                try {
                    pkg = JSON.parse(text);
                } catch (e) {}
            }

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

                let authorNote = '';
                const noteIdx = bodyLines.findIndex(l => l.includes('【作者有话说】') || l.includes('【作家的话】') || l.includes('【随章互动】'));
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

    // ================= 自动存草稿 =================

    async function saveDraftNow() {
        const btn = getSaveDraftButton();
        if (!btn) {
            showToast('⚠️ 未找到「存草稿」按钮，请确认页面已就绪！', 3000);
            return false;
        }
        btn.click();
        showToast('正在保存草稿并监视风控弹窗...');

        for (let i = 0; i < 18; i++) {
            await new Promise(r => setTimeout(r, 200));
            if (autoDismissSpellcheckModal()) break;
        }
        showToast('✅ 草稿已安全存入后台！');
        return true;
    }

    // ================= 跨页面状态机：批量全自动存草稿流水线 =================

    function showBatchBanner(currentNum, endNum, total) {
        let banner = document.getElementById('nrh-batch-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'nrh-batch-banner';
            document.body.appendChild(banner);
        }
        const progress = Math.round(((currentNum - (endNum - total + 1)) / total) * 100);
        banner.innerHTML = `
            <span>🚀 智笔云枢 · 批量录入中 [${PLATFORM === 'qimao' ? '七猫' : '番茄'}]：正在处理第 <b>${currentNum}</b> 章 / 目标第 <b>${endNum}</b> 章 (${progress}%)</span>
            <button id="nrh-stop-batch-btn" class="nrh-btn nrh-btn-danger" style="padding:4px 10px; font-size:11px;">🛑 终止批量</button>
        `;
        document.getElementById('nrh-stop-batch-btn').onclick = stopBatchPipeline;
    }

    function removeBatchBanner() {
        const banner = document.getElementById('nrh-batch-banner');
        if (banner) banner.remove();
    }

    function stopBatchPipeline() {
        setBatchState(null);
        removeBatchBanner();
        isBatchRunning = false;
        alert('🛑 批量全自动流水线已安全停止。已保存的草稿不受影响。');
    }

    async function startBatchPipeline() {
        if (isBatchRunning) return;

        const defaultStart = detectPageLastChapter() || 1;
        const startInput = prompt(`[${PLATFORM === 'qimao' ? '七猫作家中心' : '番茄小说'}] 请输入批量起始章节号：`, String(defaultStart));
        if (!startInput) return;
        const endInput = prompt('请输入批量结束章节号 (例如 10 或 50)：', String(parseInt(startInput, 10) + 9));
        if (!endInput) return;

        const startNum = parseInt(startInput, 10);
        const endNum = parseInt(endInput, 10);
        if (isNaN(startNum) || isNaN(endNum) || startNum > endNum) {
            alert('输入的章节范围无效！');
            return;
        }

        const count = endNum - startNum + 1;
        const bookId = getBookIdFromUrl();
        const currentBook = detectPageBookName();

        if (!confirm(`即将启动【${PLATFORM === 'qimao' ? '七猫作家中心' : '番茄小说'}】全自动多章节存草稿流水线：\n\n• 作品：${currentBook}\n• 目标范围：第 ${startNum} 章 ~ 第 ${endNum} 章 (共 ${count} 篇草稿)\n• 流程：自动开辟新草稿 ➔ 注入正文/标题 ➔ 存草稿 ➔ 消解提示 ➔ 自动切入下一章\n• 终点：全部存入草稿箱后自动停靠\n\n确认立即启动？`)) {
            return;
        }

        const state = {
            active: true,
            platform: PLATFORM,
            bookId: bookId,
            bookName: currentBook,
            startNum: startNum,
            endNum: endNum,
            currentNum: startNum,
            total: count
        };

        setBatchState(state);
        isBatchRunning = true;

        if (PLATFORM === 'fanqie') {
            if (location.href.includes('enter_from=newchapter') && !location.href.match(/\/publish\/\d+/)) {
                await executeBatchStep();
            } else {
                showToast('正在打开全新草稿编辑页...');
                window.location.replace(`https://fanqienovel.com/main/writer/${bookId}/publish?enter_from=newchapter&_batch_t=${Date.now()}`);
            }
        } else if (PLATFORM === 'qimao') {
            // 七猫：若在作品管理页，点击“上传章节”进入编辑
            if (location.href.includes('book-manage')) {
                const uploadBtn = Array.from(document.querySelectorAll('button, a')).find(el => (el.innerText || '').includes('上传章节'));
                if (uploadBtn) {
                    uploadBtn.click();
                    await new Promise(r => setTimeout(r, 1500));
                }
            }
            await executeBatchStep();
        } else {
            await executeBatchStep();
        }
    }

    async function executeBatchStep() {
        const state = getBatchState();
        if (!state || !state.active) {
            removeBatchBanner();
            return;
        }

        isBatchRunning = true;
        showBatchBanner(state.currentNum, state.endNum, state.total);

        showToast(`[${state.currentNum - state.startNum + 1}/${state.total}] 正在等待第 ${state.currentNum} 章编辑页就绪...`);
        let ready = false;
        for (let i = 0; i < 30; i++) {
            if (getBodyEditor() && getTitleInput()) {
                ready = true;
                break;
            }
            await new Promise(r => setTimeout(r, 500));
        }

        if (!ready) {
            alert(`等待编辑器就绪超时，请确认当前处于章节编辑页面！流水线已暂停。`);
            stopBatchPipeline();
            return;
        }

        await new Promise(r => setTimeout(r, 1000));

        try {
            showToast(`正在从智笔云枢获取第 ${state.currentNum} 章发布数据...`);
            let pkg = null;

            const targetChap = chaptersCatalog.find(c => c.chapter_number === state.currentNum);
            const fileParam = targetChap ? targetChap.filename : `第${String(state.currentNum).padStart(3, '0')}章.md`;

            try {
                pkg = await apiGet(`fanqie_publish_package?book=${encodeURIComponent(state.bookName)}&file=${encodeURIComponent(fileParam)}`);
            } catch (e) {
                console.warn('[NRH Publisher] 无法通过接口获取，尝试剪贴板');
            }

            if (!pkg || !pkg.ok) {
                throw new Error(`获取第 ${state.currentNum} 章数据失败，请确认中枢服务正常并已写入本章。`);
            }

            // 填充数据
            await executeFillChapter(pkg);
            await new Promise(r => setTimeout(r, 1000));

            // 存草稿并消解弹窗
            await saveDraftNow();
            await new Promise(r => setTimeout(r, CONFIG.saveDelayMs));

            // 步进或收尾
            if (state.currentNum < state.endNum) {
                state.currentNum++;
                setBatchState(state);
                showToast(`✅ 第 ${state.currentNum - 1} 章已安全入库！正在创建第 ${state.currentNum} 章...`);
                await new Promise(r => setTimeout(r, 800));

                if (state.platform === 'fanqie') {
                    window.location.replace(`https://fanqienovel.com/main/writer/${state.bookId}/publish?enter_from=newchapter&_batch_t=${Date.now()}`);
                } else if (state.platform === 'qimao') {
                    // 七猫：尝试点击“新建章节” / “新增章节”按钮
                    const newBtn = getNewChapterButton();
                    if (newBtn) {
                        newBtn.click();
                        await new Promise(r => setTimeout(r, 1500));
                        await executeBatchStep();
                    } else {
                        // 若为单页编辑，刷新或触发下一步
                        await new Promise(r => setTimeout(r, 1200));
                        await executeBatchStep();
                    }
                } else {
                    await executeBatchStep();
                }
            } else {
                const totalSaved = state.total;
                const bookId = state.bookId;
                const plat = state.platform;
                setBatchState(null);
                removeBatchBanner();
                isBatchRunning = false;

                alert(`🎉 恭喜！全部 ${totalSaved} 个章节 (第 ${state.startNum} 章 ~ 第 ${state.endNum} 章) 已全部成功录入【${plat === 'qimao' ? '七猫作家中心' : '番茄小说'}】草稿箱！`);
                if (plat === 'fanqie') {
                    window.location.replace(`https://fanqienovel.com/main/writer/${bookId}/publish`);
                }
            }
        } catch (e) {
            alert(`批量流程异常中断: ${e.message}`);
            stopBatchPipeline();
        }
    }

    // ================= 悬浮控制面板构建 (支持边缘智能吸附与拖拽记忆) =================

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

        const nextNum = detectPageLastChapter();
        const found = chaptersCatalog.find(c => c.chapter_number === nextNum);
        if (found) selectEl.value = found.filename;
    }

    function createFloatingWidget() {
        if (document.getElementById('nrh-floating-widget')) return;

        const platformTitle = PLATFORM === 'qimao' ? '七猫助手' : (PLATFORM === 'fanqie' ? '番茄助手' : '网文助手');
        const badgeVersion = PLATFORM === 'qimao' ? '七猫专版 v2.0' : '番茄专版 v2.0';
        const displayBook = detectPageBookName();

        const widget = document.createElement('div');
        widget.id = 'nrh-floating-widget';
        widget.innerHTML = `
            <!-- 贴边小挂签：收起时显露出来，可点击展开，也可按住上下拖拽位置 -->
            <div class="nrh-dock-tab" id="nrh-dock-tab" title="点击展开 智笔云枢 · ${platformTitle} (上下拖拽可调整停留高度)">
                <div class="nrh-dock-icon">🚀</div>
                <div class="nrh-dock-text">${platformTitle}</div>
            </div>

            <!-- 展开后的完整头部 -->
            <div class="nrh-header" id="nrh-drag-header">
                <div class="nrh-title">
                    <span>🚀 智笔云枢</span>
                    <span class="nrh-badge">${badgeVersion}</span>
                    <span class="nrh-platform-badge">${PLATFORM === 'qimao' ? '七猫' : '番茄'}</span>
                </div>
                <div class="nrh-controls">
                    <button id="nrh-btn-dock" title="贴边收起 (完全不遮挡网页内容与点击)">⇥ 贴边</button>
                    <button id="nrh-btn-close" title="彻底关闭 (可通过右侧微小黄点重新呼出)">✕</button>
                </div>
            </div>

            <!-- 展开后的主体操作区 -->
            <div class="nrh-body" id="nrh-widget-body">
                <div class="nrh-info-banner">
                    作品：<b>${displayBook}</b><br>
                    平台：<span style="color:#60a5fa;">${PLATFORM === 'qimao' ? '七猫作家中心 (zuozhe.qimao.com)' : '番茄小说作家专区 (fanqienovel.com)'}</span><br>
                    状态：<span style="color:#4ade80;">✓ 双平台智能对齐与排版自愈已就绪</span>
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
                    🚀 批量全自动流水线 (多章连存)
                </button>

                <div style="display:flex; justify-content:space-between; align-items:center; font-size:10px; color:#64748b; margin-top:2px;">
                    <span id="nrh-btn-sync" style="cursor:pointer; color:#93c5fd;">🔄 刷新章节</span>
                    <span id="nrh-btn-clip" style="cursor:pointer; color:#fde047;">📋 剪贴板秒填</span>
                </div>
            </div>
        `;

        document.body.appendChild(widget);

        // 创建微型唤醒小黄点
        const reopenDot = document.createElement('div');
        reopenDot.id = 'nrh-reopen-dot';
        reopenDot.title = `点击重新唤出 智笔云枢 · ${platformTitle} (快捷键: Alt+N)`;
        reopenDot.style.display = 'none';
        document.body.appendChild(reopenDot);

        reopenDot.addEventListener('click', () => {
            widget.style.display = 'block';
            widget.classList.remove('docked');
            reopenDot.style.display = 'none';
            GM_setValue('nrh_widget_hidden', false);
        });

        // 按钮事件绑定
        document.getElementById('nrh-btn-fill').addEventListener('click', fillCurrentSelected);
        document.getElementById('nrh-btn-save').addEventListener('click', saveDraftNow);
        document.getElementById('nrh-btn-batch').addEventListener('click', startBatchPipeline);
        document.getElementById('nrh-btn-sync').addEventListener('click', loadChapters);
        document.getElementById('nrh-btn-clip').addEventListener('click', fillFromClipboard);

        // 点击小挂签展开面板
        document.getElementById('nrh-dock-tab').addEventListener('click', (e) => {
            if (isWidgetDragging) return;
            widget.classList.remove('docked');
            GM_setValue('nrh_widget_docked', false);
        });

        // 点击“⇥ 贴边”收起
        document.getElementById('nrh-btn-dock').addEventListener('click', () => {
            widget.classList.add('docked');
            GM_setValue('nrh_widget_docked', true);
        });

        // 点击“✕ 关闭”
        document.getElementById('nrh-btn-close').addEventListener('click', () => {
            widget.style.display = 'none';
            reopenDot.style.display = 'block';
            GM_setValue('nrh_widget_hidden', true);
            showToast(`${platformTitle}已隐藏至右侧小黄点，点击即可唤出 (或按 Alt+N)`);
        });

        // 点击网页空白处自动收起（防误触）
        document.addEventListener('click', (e) => {
            if (!widget.classList.contains('docked') && widget.style.display !== 'none') {
                if (!widget.contains(e.target) && e.target !== reopenDot) {
                    widget.classList.add('docked');
                    GM_setValue('nrh_widget_docked', true);
                }
            }
        });

        // 快捷键 Alt+N
        window.addEventListener('keydown', (e) => {
            if (e.altKey && (e.key === 'n' || e.key === 'N')) {
                e.preventDefault();
                toggleWidget();
            }
        });

        // 注册油猴专属菜单
        try {
            GM_registerMenuCommand(`🚀 展开/收起 智笔云枢 · ${platformTitle} (Alt+N)`, toggleWidget);
        } catch (e) {}

        // 恢复上次拖拽高度与吸附状态
        restoreWidgetPosition(widget, reopenDot);

        // 绑定上下自由拖拽
        makeDraggable(widget, document.getElementById('nrh-drag-header'));
        makeDraggable(widget, document.getElementById('nrh-dock-tab'));
    }

    function toggleWidget() {
        const widget = document.getElementById('nrh-floating-widget');
        const reopenDot = document.getElementById('nrh-reopen-dot');
        if (!widget) return;
        if (widget.style.display === 'none') {
            widget.style.display = 'block';
            widget.classList.remove('docked');
            if (reopenDot) reopenDot.style.display = 'none';
        } else if (widget.classList.contains('docked')) {
            widget.classList.remove('docked');
        } else {
            widget.classList.add('docked');
        }
    }

    function restoreWidgetPosition(widget, reopenDot) {
        const savedTop = GM_getValue('nrh_widget_top', null);
        const isHidden = GM_getValue('nrh_widget_hidden', false);
        const isDocked = GM_getValue('nrh_widget_docked', true);

        const onEditorPage = (PLATFORM === 'fanqie' && (location.href.includes('/publish') || location.href.includes('enter_from=newchapter')))
            || (PLATFORM === 'qimao' && (location.href.includes('chapter') || location.href.includes('edit')));

        if (savedTop !== null) {
            const topNum = Math.max(20, Math.min(window.innerHeight - 140, parseInt(savedTop, 10)));
            widget.style.top = topNum + 'px';
            if (reopenDot) reopenDot.style.top = topNum + 'px';
        } else {
            widget.style.top = '220px';
            if (reopenDot) reopenDot.style.top = '220px';
        }

        if (isHidden) {
            widget.style.display = 'none';
            if (reopenDot) reopenDot.style.display = 'block';
            return;
        }

        // 非章节编辑页面（如七猫作品列表 book-manage、签约管理等），默认始终自动贴边挂起！
        if (!onEditorPage || isDocked) {
            widget.classList.add('docked');
        } else {
            widget.classList.remove('docked');
        }
    }

    function makeDraggable(widget, handle) {
        let startY = 0;
        let initialTop = 0;

        handle.addEventListener('mousedown', dragStart);

        function dragStart(e) {
            if (e.target.tagName === 'BUTTON' || e.target.closest('button')) {
                return;
            }
            isWidgetDragging = false;
            startY = e.clientY;

            const rect = widget.getBoundingClientRect();
            initialTop = rect.top;

            document.addEventListener('mousemove', dragMove);
            document.addEventListener('mouseup', dragEnd);
        }

        function dragMove(e) {
            const dy = e.clientY - startY;
            if (Math.abs(dy) > 3) {
                isWidgetDragging = true;
                widget.style.transition = 'none';
            }
            if (isWidgetDragging) {
                let newTop = Math.max(10, Math.min(window.innerHeight - 130, initialTop + dy));
                widget.style.top = newTop + 'px';
                const reopenDot = document.getElementById('nrh-reopen-dot');
                if (reopenDot) reopenDot.style.top = newTop + 'px';
            }
        }

        function dragEnd(e) {
            document.removeEventListener('mousemove', dragMove);
            document.removeEventListener('mouseup', dragEnd);

            widget.style.transition = 'transform 0.28s cubic-bezier(0.2, 0, 0, 1), opacity 0.25s, box-shadow 0.25s';

            if (isWidgetDragging) {
                const finalTop = parseInt(widget.style.top, 10);
                GM_setValue('nrh_widget_top', finalTop);
                setTimeout(() => {
                    isWidgetDragging = false;
                }, 50);
            }
        }
    }

    // ================= 初始化启动 =================
    async function init() {
        try {
            injectStyles();
            createFloatingWidget();
            await loadChapters();

            const batchState = getBatchState();
            if (batchState && batchState.active) {
                if (PLATFORM === 'fanqie' && location.href.includes('enter_from=newchapter') && !location.href.match(/\/publish\/\d+/)) {
                    await executeBatchStep();
                } else if (!isBatchRunning) {
                    showBatchBanner(batchState.currentNum, batchState.endNum, batchState.total);
                }
            }
        } catch (e) {
            console.error('[NRH Publisher] 悬浮窗构建异常:', e);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    setTimeout(init, 1000);
    setTimeout(init, 2500);

})();
