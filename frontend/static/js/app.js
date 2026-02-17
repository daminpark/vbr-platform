/* VBR Platform — Owner App (Phase 1b: AI Draft Replies) */

const API_BASE = '/api';

const state = {
    view: 'conversations',  // conversations | thread
    conversations: [],
    currentReservationId: null,
    currentThread: null,
    // AI draft state
    currentDraft: null,      // { draft, confidence, category }
    draftLoading: false,
    draftDismissed: false,
    editingDraft: null,      // tracks AI origin when editing
};

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
    const resp = await fetch(API_BASE + path, {
        headers: { 'Content-Type': 'application/json', ...opts.headers },
        ...opts,
    });
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

    if (isDesktop()) {
        renderDesktopLayout();
    } else {
        renderMobileLayout();
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

    const tabs = [
        { id: 'messages', icon: '\uD83D\uDCAC', label: 'Messages' },
        { id: 'calendar', icon: '\uD83D\uDCC5', label: 'Calendar' },
        { id: 'reviews', icon: '\u2B50', label: 'Reviews' },
        { id: 'settings', icon: '\u2699\uFE0F', label: 'Settings' },
    ];

    tabs.forEach(tab => {
        const item = el('button', 'tab-item' + (active === tab.id ? ' active' : ''));
        item.appendChild(el('span', 'tab-icon', tab.icon));
        item.appendChild(el('span', '', tab.label));
        item.addEventListener('click', () => {
            if (tab.id === 'messages') {
                state.view = 'conversations';
                render();
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

document.addEventListener('DOMContentLoaded', () => {
    loadConversations();
});
