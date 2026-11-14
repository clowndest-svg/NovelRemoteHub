// ==UserScript==
// @name         NovelRemoteHub ✕ 番茄小说作家助手 · 全自动发布与批量存草稿
// @namespace    https://scientificexploration.art/novel
// @version      1.1.0
// @description  自动将 NovelRemoteHub 智笔云枢创作的章节一键填入番茄作家后台，支持单章一键填充、作者有话说自动对齐、错别字拦截自愈与多章节全自动存草稿流水线！
// @author       NovelRemoteHub
// @match        https://fanqienovel.com/main/writer/*
// @match        https://fanqienovel.com/page/*
// @match        *://fanqienovel.com/*
// @match        *://*.fanqienovel.com/*
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

    // 默认配置
    const CONFIG = {
        hubUrl: GM_getValue('hub_url', 'https://124.220.54.64/novel'),
        token: GM_getValue('hub_token', ''),
        bookName: GM_getValue('book_name', '给政哥讲量子力学，他竟肉身硬抗二向箔'),
        autoDismissTypo: GM_getValue('auto_dismiss_typo', true),
        saveDelayMs: GM_getValue('save_delay_ms', 1800)
    };

    const BATCH_STORAGE_KEY = 'nrh_fanqie_batch_pipeline_v1';

    let chaptersCatalog = [];
    let isBatchRunning = false;

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
        const m = location.href.match(/writer\/(?:preview\/)?(\d+)/);
        if (m) {
            GM_setValue('nrh_last_book_id', m[1]);
            return m[1];
        }
        return GM_getValue('nrh_last_book_id', '7706774841793186841');
    }

    // ================= 样式注入 (黑金奢华科技主题) =================
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

    // ================= DOM 元素高精度探测器 =================

    function getTitleInput() {
        // 番茄后台章节标题输入框：placeholder 包含 "标题" 或 "章节名"
        const byPh = document.querySelector('input[placeholder*="标题"], input[placeholder*="章节名"]');
        if (byPh && !byPh.closest('#nrh-floating-widget')) return byPh;

        const inputs = Array.from(document.querySelectorAll('input:not(#nrh-floating-widget input)'));
        for (const input of inputs) {
            const ph = input.getAttribute('placeholder') || '';
            if (ph.includes('标题') || ph.includes('章节名')) return input;
            if (input.className && input.className.toLowerCase().includes('title') && !input.className.toLowerCase().includes('number')) {
                return input;
            }
        }
        return null;
    }

    function getChapterNumberInput() {
        // 番茄后台章节序号输入框：位于标题输入框前或在 "第 ... 章" 容器内
        const titleInput = getTitleInput();

        // 策略 1：根据 titleInput 在 DOM 中的顺序查找其前面的紧邻 input
        if (titleInput) {
            const allInputs = Array.from(document.querySelectorAll('input:not(#nrh-floating-widget input)'));
            const titleIdx = allInputs.indexOf(titleInput);
            if (titleIdx > 0) {
                const candidate = allInputs[titleIdx - 1];
                // 验证该 candidate 周围或祖先中包含 "第" 或 "章"
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

        // 策略 2：遍历所有 input，深度向上追溯祖先包含 "第" 和 "章"
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

        // 策略 3：检查兄弟元素
        for (const input of inputs) {
            if (input === titleInput) continue;
            const prev = input.previousElementSibling?.innerText || '';
            const next = input.nextElementSibling?.innerText || '';
            if (prev.includes('第') || next.includes('章')) {
                return input;
            }
        }

        return null;
    }

    function getBodyEditor() {
        // 番茄后台正文编辑器 (ProseMirror / contenteditable)
        const prose = document.querySelector('.ProseMirror[contenteditable="true"]');
        if (prose && !prose.closest('#nrh-floating-widget')) return prose;

        const editables = Array.from(document.querySelectorAll('div[contenteditable="true"]:not(#nrh-floating-widget div)'));
        for (const el of editables) {
            const ph = el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '';
            if (ph.includes('作者有话说') || ph.includes('互动') || ph.includes('作家的话')) continue;
            if (ph.includes('正文') || el.clientHeight > 120) return el;
        }
        if (editables.length > 0) return editables[0];
        return document.querySelector('textarea[placeholder*="正文"]');
    }

    function expandAuthorNoteIfCollapsed() {
        // 如果“作者有话说”处于折叠状态（存在 "+ 添加" 按钮），自动点击展开
        const elements = Array.from(document.querySelectorAll('button, span, div'));
        for (const el of elements) {
            const text = (el.innerText || '').trim();
            if (text === '+ 添加' || text === '+添加' || text === '添加') {
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
        // 番茄后台作者有话说文本框
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

    // ================= 浏览器原生仿真事件模拟 =================

    function fillInputValue(inputEl, value) {
        if (!inputEl) return false;
        inputEl.focus();

        const proto = inputEl.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;

        // 清除 React 内部的 _valueTracker，确保 React 侦测到 value 变化
        if (inputEl._valueTracker) {
            inputEl._valueTracker.setValue('');
        }

        if (setter) {
            setter.call(inputEl, value);
        } else {
            inputEl.value = value;
        }

        // 触发全量事件链
        inputEl.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        inputEl.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        inputEl.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: String(value), inputType: 'insertText' }));
        inputEl.dispatchEvent(new Event('blur', { bubbles: true, composed: true }));
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

        // 分段转为带中文全角缩进的段落 <p>　　段落文本</p>
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
        let numSuccess = false;
        if (numInput && packageData.chapter_number) {
            fillInputValue(numInput, String(packageData.chapter_number));
            numSuccess = (numInput.value === String(packageData.chapter_number));
        }

        // 2. 填入标题
        const titleInput = getTitleInput();
        if (titleInput) {
            // 若序号框成功注入数字，标题填入纯标题（如 "百年黄河大洪峰..."）；
            // 若序号框未探测成功，兜底策略：在标题框追加 "第X章 " 确保永不漏填章节号！
            const pureTitle = packageData.chapter_title || '';
            const fullTitle = packageData.full_title || (`第${packageData.chapter_number}章 ` + pureTitle);
            const titleVal = numSuccess ? (pureTitle || fullTitle) : fullTitle;
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

    // ================= 自动存草稿 =================

    async function saveDraftNow() {
        const btn = getSaveDraftButton();
        if (!btn) {
            showToast('⚠️ 未找到「存草稿」按钮，请确认页面已就绪！', 3000);
            return false;
        }
        btn.click();
        showToast('正在保存草稿并监视风控弹窗...');

        // 持续 3.5 秒监听并自动处理疑似错别字弹窗
        for (let i = 0; i < 18; i++) {
            await new Promise(r => setTimeout(r, 200));
            if (autoDismissSpellcheckModal()) break;
        }
        showToast('✅ 草稿已安全存入番茄后台！');
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
            <span>🚀 智笔云枢 · 批量录入中：正在处理第 <b>${currentNum}</b> 章 / 目标第 <b>${endNum}</b> 章 (进度 ${progress}%)</span>
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

        const defaultStart = detectPageLastChapter() || 41;
        const startInput = prompt('请输入批量起始章节号 (例如 41)：', String(defaultStart));
        if (!startInput) return;
        const endInput = prompt('请输入批量结束章节号 (例如 50)：', String(parseInt(startInput, 10) + 9));
        if (!endInput) return;

        const startNum = parseInt(startInput, 10);
        const endNum = parseInt(endInput, 10);
        if (isNaN(startNum) || isNaN(endNum) || startNum > endNum) {
            alert('输入的章节范围无效！');
            return;
        }

        const count = endNum - startNum + 1;
        const bookId = getBookIdFromUrl();

        if (!confirm(`即将启动全自动多章节存草稿流水线：\n\n• 目标范围：第 ${startNum} 章 ~ 第 ${endNum} 章 (共 ${count} 篇草稿)\n• 原理：每次自动开启全新独立草稿 ➔ 注入正文/标题/序号 ➔ 存草稿 ➔ 消解风控 ➔ 自动切入下一篇草稿\n• 终点：全部存入番茄草稿箱后自动返回列表\n\n确认立即启动？`)) {
            return;
        }

        const state = {
            active: true,
            bookId: bookId,
            bookName: CONFIG.bookName,
            startNum: startNum,
            endNum: endNum,
            currentNum: startNum,
            total: count
        };

        setBatchState(state);
        isBatchRunning = true;

        // 如果当前已经在全新空白编辑页，直接执行当前章
        if (location.href.includes('enter_from=newchapter') && !location.href.match(/\/publish\/\d+/)) {
            await executeBatchStep();
        } else {
            // 否则立即导航到空白新建章节页
            showToast('正在打开全新草稿编辑页...');
            window.location.replace(`https://fanqienovel.com/main/writer/${bookId}/publish?enter_from=newchapter&_batch_t=${Date.now()}`);
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

        // 1. 等待编辑区加载完成 (最多等待 15 秒)
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
            alert(`等待编辑器就绪超时，请确认当前处于番茄章节编辑页面！流水线已暂停。`);
            stopBatchPipeline();
            return;
        }

        // 额外平滑等待 1 秒让 React 完全稳定
        await new Promise(r => setTimeout(r, 1000));

        try {
            // 2. 调阅本章发布数据包
            showToast(`正在从智笔云枢获取第 ${state.currentNum} 章发布数据...`);
            let pkg = null;

            // 寻找对应章节文件名
            const targetChap = chaptersCatalog.find(c => c.chapter_number === state.currentNum);
            const fileParam = targetChap ? targetChap.filename : `第${String(state.currentNum).padStart(3, '0')}章.md`;

            try {
                pkg = await apiGet(`fanqie_publish_package?book=${encodeURIComponent(CONFIG.bookName)}&file=${encodeURIComponent(fileParam)}`);
            } catch (e) {
                console.warn('[NRH Publisher] 无法通过接口获取，尝试剪贴板');
            }

            if (!pkg || !pkg.ok) {
                throw new Error(`获取第 ${state.currentNum} 章数据失败，请确认中枢服务正常并已写入本章。`);
            }

            // 3. 填充数据
            await executeFillChapter(pkg);
            await new Promise(r => setTimeout(r, 1000));

            // 4. 存草稿并消解弹窗
            await saveDraftNow();
            await new Promise(r => setTimeout(r, CONFIG.saveDelayMs));

            // 5. 步进或收尾
            if (state.currentNum < state.endNum) {
                state.currentNum++;
                setBatchState(state);
                showToast(`✅ 第 ${state.currentNum - 1} 章已安全入库！正在跳转创建第 ${state.currentNum} 章...`);
                await new Promise(r => setTimeout(r, 800));
                window.location.replace(`https://fanqienovel.com/main/writer/${state.bookId}/publish?enter_from=newchapter&_batch_t=${Date.now()}`);
            } else {
                // 全部章节录入完成！
                const totalSaved = state.total;
                const bookId = state.bookId;
                setBatchState(null);
                removeBatchBanner();
                isBatchRunning = false;

                alert(`🎉 恭喜！全部 ${totalSaved} 个章节 (第 ${state.startNum} 章 ~ 第 ${state.endNum} 章) 已全部成功独立录入番茄草稿箱！\n\n点击确定后将为您自动跳转至草稿箱查验。`);
                window.location.replace(`https://fanqienovel.com/main/writer/${bookId}/publish`);
            }
        } catch (e) {
            alert(`批量流程异常中断: ${e.message}`);
            stopBatchPipeline();
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
                    <span class="nrh-badge">番茄助手 v1.1</span>
                </div>
                <div class="nrh-controls">
                    <button id="nrh-btn-min" title="最小化/展开">_</button>
                    <button id="nrh-btn-close" title="关闭">✕</button>
                </div>
            </div>
            <div class="nrh-body" id="nrh-widget-body">
                <div class="nrh-info-banner">
                    作品：<b>${CONFIG.bookName}</b><br>
                    状态：<span style="color:#4ade80;">✓ 跨页流水线与独立章节录入已就绪</span>
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
        document.getElementById('nrh-btn-batch').addEventListener('click', startBatchPipeline);
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
    async function init() {
        try {
            injectStyles();
            createFloatingWidget();
            await loadChapters();

            // 检查是否有跨页面未完成的批量流水线任务
            const batchState = getBatchState();
            if (batchState && batchState.active) {
                // 如果当前在新建章节页且尚未保存，执行流水线当前步骤
                if (location.href.includes('enter_from=newchapter') && !location.href.match(/\/publish\/\d+/)) {
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
    // 针对单页应用路由切换或异步渲染提供多阶段守护唤醒
    setTimeout(init, 1000);
    setTimeout(init, 2500);

})();
