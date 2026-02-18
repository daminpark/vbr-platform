/* VBR Platform — Owner + Cleaner App (Phase 1b + Inventory) */

const API_BASE = '/api';

const state = {
    authenticated: false,
    role: null,
    view: 'conversations',  // login | conversations | thread | inventory
    conversations: [],
    currentReservationId: null,
    currentThread: null,
    // AI draft state
    currentDraft: null,      // { draft, confidence, category }
    draftLoading: false,
    draftDismissed: false,
    editingDraft: null,      // tracks AI origin when editing
    // Inventory state
    invSubView: 'items',     // items | alerts | shopping | locations | bulk-import
    invItems: [],
    invLocations: [],
    invReports: [],
    invShoppingList: [],
    invSearchQuery: '',
    invSearchResults: null,
    invSearchLoading: false,
    invFilter: { house_code: null, category: null },
    invEditingItem: null,
    invBulkPreview: null,
    invBulkLoading: false,
    invNlLoading: false,
    invOwnerSearch: '',          // owner search query (client-side filter)
    invOwnerSearchResults: null, // AI search results (when client-side has no matches)
    invOwnerSearchLoading: false,
    invReportedIds: new Set(),  // items recently reported by cleaner (UI feedback)
};

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
    const resp = await fetch(API_BASE + path, {
        headers: { 'Content-Type': 'application/json', ...opts.headers },
        ...opts,
    });
    if (resp.status === 401) {
        state.authenticated = false;
        state.role = null;
        state.view = 'login';
        render();
        throw new Error('Not authenticated');
    }
    if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`${resp.status}: ${text}`);
    }
    return resp.json();
}

// ---------------------------------------------------------------------------
// Time formatting
// ---------------------------------------------------------------------------

function relativeTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMs / 3600000);
    const diffDay = Math.floor(diffMs / 86400000);

    if (diffMin < 1) return 'now';
    if (diffMin < 60) return `${diffMin}m`;
    if (diffHr < 24) return `${diffHr}h`;
    if (diffDay < 7) return `${diffDay}d`;
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

function formatTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function formatDate(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

function formatDateRange(checkIn, checkOut) {
    return formatDate(checkIn) + ' \u2014 ' + formatDate(checkOut);
}

// ---------------------------------------------------------------------------
// Layout detection
// ---------------------------------------------------------------------------

function isDesktop() {
    return window.innerWidth >= 768;
}

// ---------------------------------------------------------------------------
// Render: Main
// ---------------------------------------------------------------------------

function render() {
    const app = document.getElementById('app');
    app.textContent = '';

    if (!state.authenticated) {
        renderLogin();
        return;
    }

    if (state.view === 'inventory') {
        app.style.flexDirection = 'column';
        app.style.height = '100dvh';
        if (state.role === 'cleaner') {
            renderCleanerInventory(app);
        } else {
            renderOwnerInventory(app);
        }
        return;
    }

    if (isDesktop()) {
        renderDesktopLayout();
    } else {
        renderMobileLayout();
    }
}

// ---------------------------------------------------------------------------
// Render: Login
// ---------------------------------------------------------------------------

function renderLogin() {
    const app = document.getElementById('app');
    app.style.flexDirection = 'column';
    app.style.height = '100dvh';

    const wrap = el('div', 'login-wrap');

    const title = el('h1', 'login-title', 'VBR');
    wrap.appendChild(title);

    const subtitle = el('div', 'login-subtitle', 'Enter PIN to continue');
    wrap.appendChild(subtitle);

    const form = el('div', 'login-form');

    const input = document.createElement('input');
    input.type = 'password';
    input.className = 'login-input';
    input.placeholder = 'PIN';
    input.maxLength = 20;
    input.autocomplete = 'off';
    form.appendChild(input);

    const error = el('div', 'login-error');
    form.appendChild(error);

    const btn = el('button', 'btn login-btn', 'Unlock');
    btn.addEventListener('click', () => doLogin(input, error));
    form.appendChild(btn);

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doLogin(input, error);
    });

    wrap.appendChild(form);
    app.appendChild(wrap);

    requestAnimationFrame(() => input.focus());
}

async function doLogin(input, errorEl) {
    const pin = input.value.trim();
    if (!pin) return;

    errorEl.textContent = '';
    try {
        const resp = await fetch(API_BASE + '/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            errorEl.textContent = err.detail || 'Invalid PIN';
            input.value = '';
            input.focus();
            return;
        }
        const data = await resp.json();
        state.authenticated = true;
        state.role = data.role;
        if (data.role === 'cleaner') {
            state.view = 'inventory';
            loadInventory();
        } else {
            state.view = 'conversations';
            loadConversations();
        }
    } catch (e) {
        errorEl.textContent = 'Connection error';
    }
}

// ---------------------------------------------------------------------------
// Desktop: side-by-side conversation list + thread
// ---------------------------------------------------------------------------

function renderDesktopLayout() {
    const app = document.getElementById('app');
    app.style.flexDirection = 'row';
    app.style.height = '100vh';

    // Left sidebar — conversation list
    const sidebar = el('div', 'desktop-sidebar');
    sidebar.style.cssText = 'width:380px;min-width:320px;max-width:420px;border-right:1px solid var(--border);display:flex;flex-direction:column;height:100vh;';

    const header = el('div', 'header');
    header.appendChild(el('h1', '', 'Messages'));
    const actions = el('div', 'header-actions');
    const syncBtn = el('button', 'btn btn-ghost btn-sm', '\u21BB Sync');
    syncBtn.addEventListener('click', syncAll);
    actions.appendChild(syncBtn);
    header.appendChild(actions);
    sidebar.appendChild(header);

    const list = el('div', 'conv-list');
    renderConversationItems(list);
    sidebar.appendChild(list);

    sidebar.appendChild(renderTabBar('messages'));
    app.appendChild(sidebar);

    // Right panel — thread or empty state
    const panel = el('div', 'desktop-panel');
    panel.style.cssText = 'flex:1;display:flex;flex-direction:column;height:100vh;';

    if (state.currentThread) {
        renderThreadInto(panel, false);
    } else {
        const empty = el('div', 'empty-state');
        empty.style.flex = '1';
        empty.appendChild(el('div', 'empty-state-icon', '\uD83D\uDCAC'));
        empty.appendChild(el('div', '', 'Select a conversation'));
        panel.appendChild(empty);
    }

    app.appendChild(panel);
}

// ---------------------------------------------------------------------------
// Mobile: single view at a time
// ---------------------------------------------------------------------------

function renderMobileLayout() {
    const app = document.getElementById('app');
    app.style.flexDirection = 'column';
    app.style.height = '100dvh';

    if (state.view === 'thread' && state.currentThread) {
        renderThreadInto(app, true);
    } else {
        // Header
        const header = el('div', 'header');
        header.appendChild(el('h1', '', 'Messages'));
        const actions = el('div', 'header-actions');
        const syncBtn = el('button', 'btn btn-ghost btn-sm', '\u21BB Sync');
        syncBtn.addEventListener('click', syncAll);
        actions.appendChild(syncBtn);
        header.appendChild(actions);
        app.appendChild(header);

        // List
        const list = el('div', 'conv-list');
        renderConversationItems(list);
        app.appendChild(list);

        // Tab bar
        app.appendChild(renderTabBar('messages'));
    }
}

// ---------------------------------------------------------------------------
// Render: Conversation Items (shared between mobile & desktop)
// ---------------------------------------------------------------------------

function shortListingName(name) {
    if (!name) return '';
    // "3.1 · Room 1: The Regency | Ground Floor | Victoria" → "Room 1 · 193"
    // "193VBR · The Tachbrook: 6-Bed Historic Townhouse | Victoria" → "Whole 193"
    // "193195VBR · The Rochester: Two Townhouses, 12-Bed | Victoria" → "Both Houses"
    const lower = name.toLowerCase();
    if (lower.includes('193195') || lower.includes('rochester')) return 'Both Houses';

    // Detect house
    let house = '';
    const prefix = name.split(' ')[0].split('·')[0].trim();
    if (prefix.startsWith('3.') || prefix.startsWith('193')) house = '193';
    else if (prefix.startsWith('5.') || prefix.startsWith('195')) house = '195';

    // Whole house
    if (lower.includes('tachbrook') || lower.includes('warwick')) return 'Whole ' + house;

    // Suite
    if (lower.includes('suite')) {
        if (lower.includes('3-bed')) return '3-Bed Suite · ' + house;
        if (lower.includes('2-bed')) return '2-Bed Suite · ' + house;
        return 'Suite · ' + house;
    }

    // Room — extract room number
    const roomMatch = name.match(/Room (\d+)/i);
    if (roomMatch) {
        return 'Rm ' + roomMatch[1] + ' · ' + house;
    }

    // Berlin or other
    if (lower.includes('berlin')) return 'Berlin';

    return name.length > 25 ? name.substring(0, 22) + '...' : name;
}

function renderConversationItems(list) {
    if (state.conversations.length === 0) {
        const empty = el('div', 'empty-state');
        empty.appendChild(el('div', 'empty-state-icon', '\uD83D\uDCAC'));
        const msg = el('div', '', 'No conversations yet');
        empty.appendChild(msg);
        const sub = el('div', '', 'Tap Sync to pull from Host Tools');
        sub.style.fontSize = '0.85rem';
        empty.appendChild(sub);
        list.appendChild(empty);
        return;
    }

    state.conversations.forEach(conv => {
        const item = el('div', 'conv-item');
        if (state.currentReservationId === conv.reservation_id) {
            item.style.background = 'var(--bg-card-hover)';
        }
        item.addEventListener('click', () => openThread(conv.reservation_id));

        // Avatar
        const avatar = el('div', 'conv-avatar');
        if (conv.guest_picture_url) {
            const img = document.createElement('img');
            img.src = conv.guest_picture_url;
            img.alt = conv.guest_name;
            avatar.appendChild(img);
        } else {
            avatar.textContent = (conv.guest_name || '?')[0].toUpperCase();
        }
        item.appendChild(avatar);

        // Body
        const body = el('div', 'conv-body');

        const top = el('div', 'conv-top');
        top.appendChild(el('span', 'conv-name', conv.guest_name));
        top.appendChild(el('span', 'conv-time', relativeTime(conv.last_message_time)));
        body.appendChild(top);

        if (conv.last_message_preview) {
            const prefix = conv.last_message_sender === 'host' ? 'You: ' : '';
            body.appendChild(el('div', 'conv-preview', prefix + conv.last_message_preview));
        }

        const meta = el('div', 'conv-meta');
        if (conv.needs_attention) {
            meta.appendChild(el('span', 'conv-badge badge-attention', 'Needs reply'));
        }
        // Guest status badge
        if (conv.guest_status === 'current') {
            meta.appendChild(el('span', 'conv-badge badge-current', 'Current'));
        } else if (conv.guest_status === 'future') {
            const label = conv.status_detail === 'today' ? 'Today' : 'In ' + conv.status_detail;
            meta.appendChild(el('span', 'conv-badge badge-future', label));
        } else if (conv.guest_status === 'past') {
            meta.appendChild(el('span', 'conv-badge badge-past', conv.status_detail || 'Past'));
        }
        if (conv.listing_name) {
            meta.appendChild(el('span', 'conv-badge badge-listing', shortListingName(conv.listing_name)));
        }
        if (conv.platform) {
            meta.appendChild(el('span', 'conv-badge badge-platform', conv.platform));
        }
        body.appendChild(meta);
        item.appendChild(body);

        if (conv.needs_attention) {
            item.appendChild(el('div', 'unread-dot'));
        }

        list.appendChild(item);
    });
}

// ---------------------------------------------------------------------------
// Render: Thread (shared, rendered into a container)
// ---------------------------------------------------------------------------

function renderThreadInto(container, showBackButton) {
    const thread = state.currentThread;
    const res = thread.reservation;

    // Header
    const header = el('div', 'header');
    if (showBackButton) {
        const backBtn = el('button', 'header-back', '\u2190 Back');
        backBtn.addEventListener('click', () => {
            state.view = 'conversations';
            state.currentThread = null;
            render();
        });
        header.appendChild(backBtn);
    }
    header.appendChild(el('h1', '', res.guest_name || 'Conversation'));
    header.appendChild(el('div', '')); // spacer
    container.appendChild(header);

    // Guest context
    const ctx = el('div', 'guest-context');
    if (res.guest_picture_url) {
        const avatar = el('div', 'conv-avatar');
        avatar.style.width = '32px';
        avatar.style.height = '32px';
        avatar.style.fontSize = '0.75rem';
        const img = document.createElement('img');
        img.src = res.guest_picture_url;
        avatar.appendChild(img);
        ctx.appendChild(avatar);
    }
    const ctxInfo = el('div', 'guest-context-info');
    if (res.listing_name) {
        const s = el('span', '');
        s.appendChild(el('strong', '', res.listing_name));
        ctxInfo.appendChild(s);
    }
    ctxInfo.appendChild(el('span', '', formatDateRange(res.check_in, res.check_out)));
    if (res.num_guests) {
        ctxInfo.appendChild(el('span', '', res.num_guests + ' guest' + (res.num_guests > 1 ? 's' : '')));
    }
    if (res.platform) {
        ctxInfo.appendChild(el('span', '', res.platform));
    }
    ctx.appendChild(ctxInfo);
    container.appendChild(ctx);

    // Messages
    const threadWrap = el('div', 'thread-container');
    const scroll = el('div', 'messages-scroll');

    if (thread.messages.length === 0) {
        const empty = el('div', 'empty-state');
        empty.appendChild(el('div', '', 'No messages yet'));
        scroll.appendChild(empty);
    }

    let lastDate = '';
    thread.messages.forEach(msg => {
        const msgDate = formatDate(msg.timestamp);
        if (msgDate !== lastDate) {
            const dateSep = el('div', '');
            dateSep.textContent = msgDate;
            dateSep.style.cssText = 'text-align:center;font-size:0.7rem;color:var(--text-muted);padding:8px 0 4px;';
            scroll.appendChild(dateSep);
            lastDate = msgDate;
        }

        const bubble = el('div', 'msg-bubble ' + getMsgClass(msg));

        const bodyEl = el('div', '', msg.body);
        bubble.appendChild(bodyEl);

        // Time line
        const timeLine = el('div', 'msg-time', formatTime(msg.timestamp));
        if (msg.ai_confidence != null) {
            timeLine.appendChild(el('span', 'msg-confidence', Math.round(msg.ai_confidence * 100) + '%'));
        }
        if (msg.is_sent && msg.sender !== 'guest') {
            timeLine.appendChild(el('span', 'msg-status', '\u2713'));
        }
        bubble.appendChild(timeLine);

        // Translation toggle
        if (msg.translated && msg.body_original) {
            const toggle = el('div', '');
            toggle.style.cssText = 'font-size:0.7rem;color:var(--accent);cursor:pointer;margin-top:4px;';
            toggle.textContent = 'Show original';
            let showOrig = false;
            toggle.addEventListener('click', (e) => {
                e.stopPropagation();
                showOrig = !showOrig;
                bodyEl.textContent = showOrig ? msg.body_original : msg.body;
                toggle.textContent = showOrig ? 'Show translation' : 'Show original';
            });
            bubble.appendChild(toggle);
        }

        scroll.appendChild(bubble);
    });

    threadWrap.appendChild(scroll);

    // AI Draft panel (above compose bar)
    if (state.draftLoading) {
        const loading = el('div', 'draft-panel');
        const inner = el('div', 'draft-loading');
        inner.appendChild(el('div', 'spinner'));
        inner.appendChild(el('span', '', 'Generating draft...'));
        loading.appendChild(inner);
        threadWrap.appendChild(loading);
    } else if (state.currentDraft && !state.draftDismissed) {
        const draftPanel = el('div', 'draft-panel');

        // Header: "AI Draft" + confidence badge
        const draftHeader = el('div', 'draft-header');
        draftHeader.appendChild(el('span', 'draft-label', 'AI Draft'));
        if (state.currentDraft.confidence != null) {
            const pct = Math.round(state.currentDraft.confidence * 100);
            draftHeader.appendChild(el('span', 'draft-confidence', pct + '%'));
        }
        draftPanel.appendChild(draftHeader);

        // Draft body
        const draftBody = el('div', 'draft-body');
        draftBody.textContent = state.currentDraft.draft;
        draftPanel.appendChild(draftBody);

        // Action buttons
        const draftActions = el('div', 'draft-actions');

        const useBtn = el('button', 'btn btn-sm draft-btn-use', 'Send');
        useBtn.addEventListener('click', sendDraftAsIs);

        const editBtn = el('button', 'btn btn-sm draft-btn-edit', 'Edit');
        editBtn.addEventListener('click', editDraft);

        const regenBtn = el('button', 'btn btn-sm draft-btn-regen', 'Regenerate');
        regenBtn.addEventListener('click', () => generateDraft(state.currentReservationId));

        const dismissBtn = el('button', 'btn btn-sm draft-btn-dismiss', 'Dismiss');
        dismissBtn.addEventListener('click', () => {
            state.draftDismissed = true;
            render();
        });

        draftActions.appendChild(useBtn);
        draftActions.appendChild(editBtn);
        draftActions.appendChild(regenBtn);
        draftActions.appendChild(dismissBtn);
        draftPanel.appendChild(draftActions);

        threadWrap.appendChild(draftPanel);
    }

    // Compose bar
    const compose = el('div', 'compose-bar');
    const input = document.createElement('textarea');
    input.className = 'compose-input';
    input.placeholder = 'Type a message...';
    input.rows = 1;
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(input);
        }
    });
    compose.appendChild(input);

    const sendBtn = el('button', 'compose-send', '\u2191');
    sendBtn.addEventListener('click', () => sendMessage(input));
    compose.appendChild(sendBtn);
    threadWrap.appendChild(compose);

    container.appendChild(threadWrap);

    // Scroll to bottom
    requestAnimationFrame(() => {
        scroll.scrollTop = scroll.scrollHeight;
    });
}

function getMsgClass(msg) {
    if (msg.sender === 'guest') return 'msg-guest';
    if (msg.is_template) return 'msg-template';
    if (msg.ai_auto_sent) return 'msg-ai-auto';
    if (msg.ai_generated) return 'msg-ai-draft-sent';
    return 'msg-host';
}

// ---------------------------------------------------------------------------
// Render: Tab Bar
// ---------------------------------------------------------------------------

function renderTabBar(active) {
    const bar = el('div', 'tab-bar');

    let tabs;
    if (state.role === 'cleaner') {
        tabs = [
            { id: 'inventory', icon: '\uD83D\uDD0D', label: 'Find' },
            { id: 'reports', icon: '\uD83D\uDCE2', label: 'Report' },
        ];
    } else {
        tabs = [
            { id: 'messages', icon: '\uD83D\uDCAC', label: 'Messages' },
            { id: 'inventory', icon: '\uD83D\uDCE6', label: 'Inventory' },
            { id: 'calendar', icon: '\uD83D\uDCC5', label: 'Calendar' },
            { id: 'settings', icon: '\u2699\uFE0F', label: 'Settings' },
        ];
    }

    tabs.forEach(tab => {
        const item = el('button', 'tab-item' + (active === tab.id ? ' active' : ''));
        item.appendChild(el('span', 'tab-icon', tab.icon));
        item.appendChild(el('span', '', tab.label));
        item.addEventListener('click', () => {
            if (tab.id === 'messages') {
                state.view = 'conversations';
                state.currentThread = null;
                loadConversations();
            } else if (tab.id === 'inventory') {
                state.view = 'inventory';
                loadInventory();
            } else if (tab.id === 'reports') {
                state.view = 'inventory';
                state.invSubView = 'alerts';
                loadInventory();
            }
        });
        bar.appendChild(item);
    });

    return bar;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async function loadConversations() {
    try {
        state.conversations = await api('/conversations');
    } catch (e) {
        console.error('Failed to load conversations:', e);
        state.conversations = [];
    }
    render();
}

async function openThread(reservationId) {
    state.currentReservationId = reservationId;
    state.view = 'thread';
    state.currentDraft = null;
    state.draftDismissed = false;
    state.draftLoading = false;
    state.editingDraft = null;

    // Show loading in the right place
    if (isDesktop()) {
        // Re-render list with selection highlight, show loading in panel
        render();
    }

    try {
        state.currentThread = await api('/conversations/' + reservationId + '/messages');
    } catch (e) {
        console.error('Failed to load thread:', e);
        state.currentThread = { reservation: {}, messages: [] };
    }
    render();

    // Auto-generate draft if last message is from guest
    const msgs = state.currentThread.messages || [];
    const lastMsg = msgs[msgs.length - 1];
    if (lastMsg && lastMsg.sender === 'guest' && !lastMsg.is_template) {
        generateDraft(reservationId);
    }
}

async function sendMessage(inputEl) {
    const body = inputEl.value.trim();
    if (!body || !state.currentReservationId) return;

    inputEl.value = '';
    inputEl.style.height = 'auto';

    // Build send payload — check if this was an edited AI draft
    const sendData = { body };
    if (state.editingDraft) {
        sendData.was_edited = true;
        sendData.original_ai_draft = state.editingDraft.draft;
        sendData.ai_confidence = state.editingDraft.confidence;
        sendData.ai_category = state.editingDraft.category;
        state.editingDraft = null;
    }

    try {
        await api('/conversations/' + state.currentReservationId + '/send', {
            method: 'POST',
            body: JSON.stringify(sendData),
        });
        await openThread(state.currentReservationId);
    } catch (e) {
        console.error('Failed to send message:', e);
        alert('Failed to send: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// AI Draft Actions
// ---------------------------------------------------------------------------

async function generateDraft(reservationId) {
    state.draftLoading = true;
    state.currentDraft = null;
    state.draftDismissed = false;
    render();

    try {
        state.currentDraft = await api('/conversations/' + reservationId + '/draft', {
            method: 'POST',
        });
    } catch (e) {
        console.error('Failed to generate draft:', e);
        state.currentDraft = null;
    }
    state.draftLoading = false;
    render();
}

async function sendDraftAsIs() {
    if (!state.currentDraft || !state.currentReservationId) return;

    const draft = state.currentDraft;
    state.currentDraft = null;
    state.draftDismissed = true;

    try {
        await api('/conversations/' + state.currentReservationId + '/send', {
            method: 'POST',
            body: JSON.stringify({
                body: draft.draft,
                was_edited: false,
                original_ai_draft: draft.draft,
                ai_confidence: draft.confidence,
                ai_category: draft.category,
            }),
        });
        await openThread(state.currentReservationId);
    } catch (e) {
        console.error('Failed to send draft:', e);
        alert('Failed to send: ' + e.message);
    }
}

function editDraft() {
    if (!state.currentDraft) return;
    // Store the draft for tracking when they hit send
    state.editingDraft = state.currentDraft;
    state.draftDismissed = true;
    render();

    // Populate the compose textarea after render
    requestAnimationFrame(() => {
        const input = document.querySelector('.compose-input');
        if (input) {
            input.value = state.editingDraft.draft;
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 120) + 'px';
            input.focus();
        }
    });
}

// ---------------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------------

async function syncAll() {
    const syncBar = el('div', 'sync-bar', 'Syncing listings...');
    document.getElementById('app').prepend(syncBar);

    try {
        await api('/sync/listings', { method: 'POST' });
        syncBar.textContent = 'Syncing reservations...';
        await api('/sync/reservations', { method: 'POST' });
        syncBar.textContent = 'Done!';
        setTimeout(() => { syncBar.remove(); loadConversations(); }, 500);
    } catch (e) {
        syncBar.textContent = 'Sync failed: ' + e.message;
        syncBar.style.background = 'var(--red-dim)';
        syncBar.style.color = 'var(--red)';
        setTimeout(() => syncBar.remove(), 3000);
    }
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function el(tag, className, textContent) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (textContent) e.textContent = textContent;
    return e;
}

// ---------------------------------------------------------------------------
// Inventory: Data Loading
// ---------------------------------------------------------------------------

async function loadInventory() {
    try {
        const [items, locations] = await Promise.all([
            api('/inventory/items'),
            api('/inventory/locations'),
        ]);
        state.invItems = items;
        state.invLocations = locations;
    } catch (e) {
        console.error('Failed to load inventory:', e);
        state.invItems = [];
        state.invLocations = [];
    }
    render();
}

async function loadReports() {
    try {
        state.invReports = await api('/inventory/reports?resolved=false');
    } catch (e) {
        console.error('Failed to load reports:', e);
        state.invReports = [];
    }
}

async function loadShoppingList() {
    try {
        state.invShoppingList = await api('/inventory/shopping-list');
    } catch (e) {
        console.error('Failed to load shopping list:', e);
        state.invShoppingList = [];
    }
}

function addSelectOption(selectEl, value, label) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    selectEl.appendChild(opt);
}

// ---------------------------------------------------------------------------
// Inventory: Owner View
// ---------------------------------------------------------------------------

function renderOwnerInventory(app) {
    // Header
    const header = el('div', 'header');
    header.appendChild(el('h1', '', 'Inventory'));
    app.appendChild(header);

    // Sub-nav pills
    const subNav = el('div', 'inv-subnav');
    const subViews = [
        { id: 'items', label: 'All Items' },
        { id: 'alerts', label: 'Alerts' },
        { id: 'shopping', label: 'Shopping' },
        { id: 'locations', label: 'Locations' },
        { id: 'bulk-import', label: 'Import' },
    ];
    subViews.forEach(sv => {
        const pill = el('button', 'inv-pill' + (state.invSubView === sv.id ? ' active' : ''), sv.label);
        pill.addEventListener('click', async () => {
            state.invSubView = sv.id;
            if (sv.id === 'alerts') await loadReports();
            if (sv.id === 'shopping') await loadShoppingList();
            render();
        });
        subNav.appendChild(pill);
    });
    app.appendChild(subNav);

    // Content area
    const content = el('div', 'inv-content');

    if (state.invSubView === 'items') {
        renderOwnerItemsList(content);
    } else if (state.invSubView === 'alerts') {
        renderOwnerAlerts(content);
    } else if (state.invSubView === 'shopping') {
        renderOwnerShopping(content);
    } else if (state.invSubView === 'locations') {
        renderOwnerLocations(content);
    } else if (state.invSubView === 'bulk-import') {
        renderBulkImport(content);
    }

    app.appendChild(content);
    app.appendChild(renderTabBar('inventory'));
}

function renderOwnerItemsList(container) {
    // Search bar
    const searchBar = el('div', 'inv-search-bar');
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'inv-search-input';
    searchInput.placeholder = 'Search items... "drain stuff", "bleach"';
    searchInput.value = state.invOwnerSearch;

    let ownerSearchTimeout;
    searchInput.addEventListener('input', () => {
        state.invOwnerSearch = searchInput.value;
        state.invOwnerSearchResults = null;
        clearTimeout(ownerSearchTimeout);
        ownerSearchTimeout = setTimeout(() => render(), 150);
    });
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && searchInput.value.trim().length >= 2) {
            // On Enter, do AI search if local results are sparse
            doOwnerSearch(searchInput.value.trim());
        }
    });
    searchBar.appendChild(searchInput);
    container.appendChild(searchBar);

    // AI Input bar (add items)
    const inputBar = el('div', 'inv-ai-bar');
    const nlInput = document.createElement('input');
    nlInput.type = 'text';
    nlInput.className = 'inv-search-input';
    nlInput.placeholder = 'Add... "3 sponges in 195 kitchen"';
    nlInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') aiParseAndAdd(nlInput);
    });
    inputBar.appendChild(nlInput);
    const addBtn = el('button', 'compose-send', '+');
    addBtn.addEventListener('click', () => aiParseAndAdd(nlInput));
    inputBar.appendChild(addBtn);
    container.appendChild(inputBar);

    if (state.invNlLoading) {
        const ld = el('div', 'loading');
        ld.appendChild(el('div', 'spinner'));
        ld.appendChild(el('span', '', 'Parsing...'));
        container.appendChild(ld);
    }

    // If AI search returned results, show those instead
    if (state.invOwnerSearchLoading) {
        const ld = el('div', 'loading');
        ld.appendChild(el('div', 'spinner'));
        ld.appendChild(el('span', '', 'AI searching...'));
        container.appendChild(ld);
        return;
    }

    if (state.invOwnerSearchResults !== null) {
        const hint = el('div', 'inv-filters');
        hint.appendChild(el('span', 'inv-count', state.invOwnerSearchResults.length + ' AI result' + (state.invOwnerSearchResults.length !== 1 ? 's' : '')));
        const clearBtn = el('button', 'btn btn-ghost btn-sm', 'Clear');
        clearBtn.addEventListener('click', () => {
            state.invOwnerSearch = '';
            state.invOwnerSearchResults = null;
            render();
        });
        hint.appendChild(clearBtn);
        container.appendChild(hint);
        renderOwnerItemRows(container, state.invOwnerSearchResults);
        return;
    }

    // Filter row
    const filterRow = el('div', 'inv-filters');
    const houseSelect = document.createElement('select');
    houseSelect.className = 'inv-select';
    addSelectOption(houseSelect, '', 'All Houses');
    addSelectOption(houseSelect, '193', '193');
    addSelectOption(houseSelect, '195', '195');
    houseSelect.value = state.invFilter.house_code || '';
    houseSelect.addEventListener('change', () => {
        state.invFilter.house_code = houseSelect.value || null;
        render();
    });
    filterRow.appendChild(houseSelect);

    const catSelect = document.createElement('select');
    catSelect.className = 'inv-select';
    addSelectOption(catSelect, '', 'All Categories');
    const cats = [...new Set(state.invItems.map(i => i.category))].sort();
    cats.forEach(c => addSelectOption(catSelect, c, c));
    catSelect.value = state.invFilter.category || '';
    catSelect.addEventListener('change', () => {
        state.invFilter.category = catSelect.value || null;
        render();
    });
    filterRow.appendChild(catSelect);

    const countEl = el('span', 'inv-count');
    filterRow.appendChild(countEl);
    container.appendChild(filterRow);

    // Filtered items
    let items = state.invItems;
    if (state.invFilter.house_code) {
        items = items.filter(i => i.house_code === state.invFilter.house_code);
    }
    if (state.invFilter.category) {
        items = items.filter(i => i.category === state.invFilter.category);
    }

    // Client-side search filter
    const q = state.invOwnerSearch.trim().toLowerCase();
    if (q.length >= 2) {
        items = items.filter(i =>
            i.name.toLowerCase().includes(q) ||
            (i.category && i.category.toLowerCase().includes(q)) ||
            (i.brand && i.brand.toLowerCase().includes(q)) ||
            (i.location_name && i.location_name.toLowerCase().includes(q))
        );
    }

    countEl.textContent = items.length + ' item' + (items.length !== 1 ? 's' : '');

    if (items.length === 0 && q.length >= 2) {
        const empty = el('div', 'empty-state');
        empty.appendChild(el('div', '', 'No local matches'));
        const aiHint = el('div', 'inv-hint', 'Press Enter to AI search');
        empty.appendChild(aiHint);
        container.appendChild(empty);
        return;
    }

    if (items.length === 0) {
        const empty = el('div', 'empty-state');
        empty.appendChild(el('div', 'empty-state-icon', '\uD83D\uDCE6'));
        empty.appendChild(el('div', '', 'No items yet'));
        empty.appendChild(el('div', 'inv-hint', 'Use Import tab or the add bar above'));
        container.appendChild(empty);
        return;
    }

    renderOwnerItemRows(container, items);
}

function renderOwnerItemRows(container, items) {
    const list = el('div', 'inv-list');
    items.forEach(item => {
        const row = el('div', 'inv-item');

        const info = el('div', 'inv-item-info');
        const nameRow = el('div', 'inv-item-top');
        nameRow.appendChild(el('span', 'inv-item-name', item.name));
        if (item.quantity > 1) {
            nameRow.appendChild(el('span', 'inv-item-qty', 'x' + item.quantity + (item.unit ? ' ' + item.unit : '')));
        }
        info.appendChild(nameRow);

        const meta = el('div', 'conv-meta');
        if (item.location_name) {
            const locLabel = (item.house_code || '') + ' ' + item.location_name;
            meta.appendChild(el('span', 'conv-badge badge-listing', locLabel.trim()));
        }
        meta.appendChild(el('span', 'conv-badge badge-platform', item.category));
        if (item.has_alert) {
            meta.appendChild(el('span', 'conv-badge badge-attention', item.alert_count + ' alert' + (item.alert_count > 1 ? 's' : '')));
        }
        if (item.brand) {
            meta.appendChild(el('span', 'conv-badge badge-past', item.brand));
        }
        info.appendChild(meta);
        row.appendChild(info);

        list.appendChild(row);
    });
    container.appendChild(list);
}

function renderOwnerAlerts(container) {
    if (state.invReports.length === 0) {
        const empty = el('div', 'empty-state');
        empty.appendChild(el('div', 'empty-state-icon', '\u2705'));
        empty.appendChild(el('div', '', 'No unresolved alerts'));
        container.appendChild(empty);
        return;
    }

    const list = el('div', 'inv-list');
    state.invReports.forEach(report => {
        const row = el('div', 'inv-item');

        const info = el('div', 'inv-item-info');
        const nameRow = el('div', 'inv-item-top');
        nameRow.appendChild(el('span', 'inv-item-name', report.item_name || 'Unknown'));
        nameRow.appendChild(el('span', 'conv-time', relativeTime(report.created_at)));
        info.appendChild(nameRow);

        const meta = el('div', 'conv-meta');
        const typeClass = report.report_type === 'missing' ? 'badge-attention' : 'badge-future';
        meta.appendChild(el('span', 'conv-badge ' + typeClass, report.report_type.toUpperCase()));
        if (report.location_name) {
            meta.appendChild(el('span', 'conv-badge badge-listing', (report.house_code || '') + ' ' + report.location_name));
        }
        meta.appendChild(el('span', 'conv-badge badge-past', 'by ' + report.reported_by));
        info.appendChild(meta);

        if (report.notes) {
            info.appendChild(el('div', 'conv-preview', report.notes));
        }
        row.appendChild(info);

        const resolveBtn = el('button', 'btn btn-sm btn-ghost', '\u2713 Resolve');
        resolveBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                await api('/inventory/reports/' + report.id + '/resolve', { method: 'PUT' });
                await loadReports();
                render();
            } catch (err) {
                alert('Failed: ' + err.message);
            }
        });
        row.appendChild(resolveBtn);

        list.appendChild(row);
    });
    container.appendChild(list);
}

function renderOwnerShopping(container) {
    if (state.invShoppingList.length === 0) {
        const empty = el('div', 'empty-state');
        empty.appendChild(el('div', 'empty-state-icon', '\uD83D\uDED2'));
        empty.appendChild(el('div', '', 'Shopping list is empty'));
        container.appendChild(empty);
        return;
    }

    const list = el('div', 'inv-list');
    state.invShoppingList.forEach(entry => {
        const row = el('div', 'inv-item');

        const info = el('div', 'inv-item-info');
        const nameRow = el('div', 'inv-item-top');
        nameRow.appendChild(el('span', 'inv-item-name', entry.name));
        info.appendChild(nameRow);

        const meta = el('div', 'conv-meta');
        const statusClass = entry.worst_status === 'missing' ? 'badge-attention' : 'badge-future';
        meta.appendChild(el('span', 'conv-badge ' + statusClass, entry.worst_status));
        if (entry.house_code) {
            meta.appendChild(el('span', 'conv-badge badge-listing', entry.house_code + ' ' + (entry.location_name || '')));
        }
        if (entry.brand) {
            meta.appendChild(el('span', 'conv-badge badge-past', entry.brand));
        }
        info.appendChild(meta);
        row.appendChild(info);

        if (entry.purchase_url) {
            const buyBtn = el('button', 'btn btn-sm btn-primary', 'Buy');
            buyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                window.open(entry.purchase_url, '_blank');
            });
            row.appendChild(buyBtn);
        }

        list.appendChild(row);
    });
    container.appendChild(list);
}

function renderOwnerLocations(container) {
    if (state.invLocations.length === 0) {
        const empty = el('div', 'empty-state');
        empty.appendChild(el('div', 'empty-state-icon', '\uD83D\uDDC2\uFE0F'));
        empty.appendChild(el('div', '', 'No locations. Seed them first.'));
        container.appendChild(empty);
        return;
    }

    const list = el('div', 'inv-list');
    state.invLocations.forEach(loc => {
        const row = el('div', 'inv-item');
        const info = el('div', 'inv-item-info');
        const nameRow = el('div', 'inv-item-top');
        nameRow.appendChild(el('span', 'inv-item-name', loc.house_code + ' ' + loc.name));
        nameRow.appendChild(el('span', 'inv-item-qty', loc.item_count + ' items'));
        info.appendChild(nameRow);

        const meta = el('div', 'conv-meta');
        if (loc.outdoor) meta.appendChild(el('span', 'conv-badge badge-future', 'outdoor'));
        if (loc.locked) meta.appendChild(el('span', 'conv-badge badge-attention', 'locked'));
        if (loc.guest_accessible) meta.appendChild(el('span', 'conv-badge badge-current', 'guest-accessible'));
        info.appendChild(meta);

        if (loc.description) {
            info.appendChild(el('div', 'conv-preview', loc.description));
        }

        row.appendChild(info);

        // Children
        if (loc.children && loc.children.length > 0) {
            const childList = el('div', 'inv-children');
            loc.children.forEach(child => {
                const childRow = el('div', 'inv-child');
                childRow.appendChild(el('span', '', '\u2514 ' + child.name));
                childRow.appendChild(el('span', 'inv-item-qty', child.item_count + ' items'));
                childList.appendChild(childRow);
            });
            row.appendChild(childList);
        }

        list.appendChild(row);
    });
    container.appendChild(list);
}

function renderBulkImport(container) {
    const wrap = el('div', 'inv-bulk-wrap');

    if (!state.invBulkPreview) {
        wrap.appendChild(el('div', 'inv-bulk-hint', 'Paste your inventory list below. Format: location, then items. AI will parse it.'));

        const textarea = document.createElement('textarea');
        textarea.className = 'inv-bulk-textarea';
        textarea.placeholder = '193 toolshed: wd40, electrical tape, spare bulbs\n195 under kitchen sink: fairy, bleach, drain unblocker\ncleaning room: 3x bottles of bleach, sponges';
        textarea.rows = 10;
        wrap.appendChild(textarea);

        const btnRow = el('div', 'inv-bulk-actions');
        const parseBtn = el('button', 'btn btn-primary', state.invBulkLoading ? 'Parsing...' : 'Parse with AI');
        if (state.invBulkLoading) parseBtn.disabled = true;
        parseBtn.addEventListener('click', async () => {
            const text = textarea.value.trim();
            if (!text) return;
            state.invBulkLoading = true;
            render();
            try {
                state.invBulkPreview = await api('/inventory/ai/bulk-import', {
                    method: 'POST',
                    body: JSON.stringify({ text }),
                });
            } catch (e) {
                alert('Parse failed: ' + e.message);
            }
            state.invBulkLoading = false;
            render();
        });
        btnRow.appendChild(parseBtn);
        wrap.appendChild(btnRow);
    } else {
        // Preview parsed items
        const items = state.invBulkPreview.items || [];
        wrap.appendChild(el('div', 'inv-bulk-hint', items.length + ' items parsed. Review and confirm:'));

        const previewList = el('div', 'inv-list');
        items.forEach((item, i) => {
            const row = el('div', 'inv-item');
            const info = el('div', 'inv-item-info');
            const nameRow = el('div', 'inv-item-top');
            nameRow.appendChild(el('span', 'inv-item-name', item.name));
            if (item.quantity > 1) {
                nameRow.appendChild(el('span', 'inv-item-qty', 'x' + item.quantity + (item.unit ? ' ' + item.unit : '')));
            }
            info.appendChild(nameRow);

            const meta = el('div', 'conv-meta');
            if (item.location_code || item.location_name) {
                meta.appendChild(el('span', 'conv-badge badge-listing', item.location_code || item.location_name));
            }
            meta.appendChild(el('span', 'conv-badge badge-platform', item.category));
            info.appendChild(meta);
            row.appendChild(info);

            const removeBtn = el('button', 'btn btn-sm btn-ghost', '\u2715');
            removeBtn.style.color = 'var(--red)';
            removeBtn.addEventListener('click', () => {
                items.splice(i, 1);
                render();
            });
            row.appendChild(removeBtn);

            previewList.appendChild(row);
        });
        wrap.appendChild(previewList);

        const btnRow = el('div', 'inv-bulk-actions');
        const confirmBtn = el('button', 'btn btn-primary', 'Import ' + items.length + ' Items');
        confirmBtn.addEventListener('click', async () => {
            try {
                await api('/inventory/ai/bulk-import/confirm', {
                    method: 'POST',
                    body: JSON.stringify({ items }),
                });
                state.invBulkPreview = null;
                state.invSubView = 'items';
                loadInventory();
            } catch (e) {
                alert('Import failed: ' + e.message);
            }
        });
        const cancelBtn = el('button', 'btn btn-ghost', 'Cancel');
        cancelBtn.addEventListener('click', () => {
            state.invBulkPreview = null;
            render();
        });
        btnRow.appendChild(confirmBtn);
        btnRow.appendChild(cancelBtn);
        wrap.appendChild(btnRow);
    }

    container.appendChild(wrap);
}

async function aiParseAndAdd(inputEl) {
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';

    state.invNlLoading = true;
    render();

    try {
        const parsed = await api('/inventory/ai/parse', {
            method: 'POST',
            body: JSON.stringify({ text }),
        });
        const items = parsed.items || [];
        if (items.length === 0) {
            alert('Could not parse input. Try being more specific.');
            state.invNlLoading = false;
            render();
            return;
        }
        // Auto-confirm: add all parsed items
        await api('/inventory/ai/bulk-import/confirm', {
            method: 'POST',
            body: JSON.stringify({ items }),
        });
        state.invNlLoading = false;
        loadInventory();
    } catch (e) {
        alert('Failed: ' + e.message);
        state.invNlLoading = false;
        render();
    }
}

async function doOwnerSearch(query) {
    state.invOwnerSearchLoading = true;
    render();

    try {
        state.invOwnerSearchResults = await api('/inventory/search', {
            method: 'POST',
            body: JSON.stringify({ query }),
        });
    } catch (e) {
        console.error('Owner search failed:', e);
        state.invOwnerSearchResults = [];
    }
    state.invOwnerSearchLoading = false;
    render();
}

// ---------------------------------------------------------------------------
// Inventory: Cleaner View
// ---------------------------------------------------------------------------

function renderCleanerInventory(app) {
    // Header
    const header = el('div', 'header');
    header.appendChild(el('h1', '', 'Find Stuff'));
    app.appendChild(header);

    // Search bar
    const searchBar = el('div', 'inv-search-bar');
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'inv-search-input inv-search-lg';
    searchInput.placeholder = 'Search... e.g. "drain stuff", "loo roll"';
    searchInput.value = state.invSearchQuery;

    let searchTimeout;
    searchInput.addEventListener('input', () => {
        state.invSearchQuery = searchInput.value;
        clearTimeout(searchTimeout);
        if (searchInput.value.trim().length >= 2) {
            searchTimeout = setTimeout(() => doInventorySearch(searchInput.value.trim()), 300);
        } else {
            state.invSearchResults = null;
            render();
        }
    });
    searchBar.appendChild(searchInput);
    app.appendChild(searchBar);

    // Content
    const content = el('div', 'inv-content');

    if (state.invSearchLoading) {
        const ld = el('div', 'loading');
        ld.appendChild(el('div', 'spinner'));
        ld.appendChild(el('span', '', 'Searching...'));
        content.appendChild(ld);
    } else if (state.invSearchResults !== null) {
        // Search results
        if (state.invSearchResults.length === 0) {
            const empty = el('div', 'empty-state');
            empty.appendChild(el('div', '', 'No results for "' + state.invSearchQuery + '"'));
            content.appendChild(empty);
        } else {
            renderCleanerItemList(content, state.invSearchResults);
        }
    } else {
        // Browse by location
        renderCleanerLocationBrowse(content);
    }

    app.appendChild(content);
    app.appendChild(renderTabBar('inventory'));

    // Focus search on render
    requestAnimationFrame(() => searchInput.focus());
}

function renderCleanerItemList(container, items) {
    const list = el('div', 'inv-list');
    items.forEach(item => {
        const row = el('div', 'inv-item');

        const info = el('div', 'inv-item-info');
        const nameRow = el('div', 'inv-item-top');
        nameRow.appendChild(el('span', 'inv-item-name', item.name));
        if (item.quantity > 1) {
            nameRow.appendChild(el('span', 'inv-item-qty', 'x' + item.quantity));
        }
        info.appendChild(nameRow);

        const meta = el('div', 'conv-meta');
        if (item.location_name) {
            const locLabel = (item.house_code || '') + ' ' + item.location_name;
            meta.appendChild(el('span', 'conv-badge badge-listing', locLabel.trim()));
        }
        info.appendChild(meta);
        row.appendChild(info);

        // Action buttons
        const actionsDiv = el('div', 'inv-actions');
        const reported = state.invReportedIds.has(item.id);

        if (reported) {
            const doneBtn = el('button', 'btn btn-sm btn-reported', '\u2713 Reported');
            actionsDiv.appendChild(doneBtn);
        } else {
            const lowBtn = el('button', 'btn btn-sm btn-low', 'Low');
            lowBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                reportStock(item.id, 'low');
            });
            actionsDiv.appendChild(lowBtn);

            const missingBtn = el('button', 'btn btn-sm btn-missing', 'Out');
            missingBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                reportStock(item.id, 'missing');
            });
            actionsDiv.appendChild(missingBtn);
        }
        row.appendChild(actionsDiv);

        list.appendChild(row);
    });
    container.appendChild(list);
}

function renderCleanerLocationBrowse(container) {
    if (state.invLocations.length === 0) {
        const empty = el('div', 'empty-state');
        empty.appendChild(el('div', '', 'No locations loaded'));
        container.appendChild(empty);
        return;
    }

    // Flatten locations with their items for browsing
    const flatLocations = [];
    state.invLocations.forEach(loc => {
        flatLocations.push(loc);
        if (loc.children) {
            loc.children.forEach(child => flatLocations.push(child));
        }
    });

    container.appendChild(el('div', 'inv-browse-hint', 'Browse by location'));
    const list = el('div', 'inv-list');
    flatLocations.forEach(loc => {
        const row = el('div', 'inv-item inv-loc-row');
        row.addEventListener('click', async () => {
            // Search by location name to show its items
            state.invSearchQuery = '';
            try {
                state.invSearchResults = await api('/inventory/items?location_id=' + loc.id);
            } catch (e) {
                state.invSearchResults = [];
            }
            render();
        });
        const info = el('div', 'inv-item-info');
        info.appendChild(el('span', 'inv-item-name', loc.house_code + ' ' + loc.name));
        info.appendChild(el('span', 'inv-item-qty', loc.item_count + ' items'));
        row.appendChild(info);
        row.appendChild(el('span', 'inv-chevron', '\u203A'));
        list.appendChild(row);
    });
    container.appendChild(list);
}

async function doInventorySearch(query) {
    state.invSearchLoading = true;
    render();

    try {
        state.invSearchResults = await api('/inventory/search', {
            method: 'POST',
            body: JSON.stringify({ query }),
        });
    } catch (e) {
        console.error('Search failed:', e);
        state.invSearchResults = [];
    }
    state.invSearchLoading = false;
    render();
}

async function reportStock(itemId, reportType) {
    try {
        await api('/inventory/reports', {
            method: 'POST',
            body: JSON.stringify({ item_id: itemId, report_type: reportType }),
        });
        state.invReportedIds.add(itemId);
        render();
    } catch (e) {
        alert('Failed to report: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// Responsive: re-render on resize
// ---------------------------------------------------------------------------

let resizeTimeout;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(render, 150);
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
    // Check existing session
    try {
        const resp = await fetch(API_BASE + '/auth/check');
        const data = await resp.json();
        if (data.authenticated) {
            state.authenticated = true;
            state.role = data.role;
            if (data.role === 'cleaner') {
                state.view = 'inventory';
                loadInventory();
            } else {
                loadConversations();
            }
            return;
        }
    } catch (e) {
        // Server unreachable — show login
    }
    state.authenticated = false;
    render();
});
