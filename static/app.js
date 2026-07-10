document.addEventListener("DOMContentLoaded", () => {
    const publicShell = document.getElementById("publicShell");
    const privateApp = document.getElementById("privateApp");
    const unlockForm = document.getElementById("unlockForm");
    const unlockToken = document.getElementById("unlockToken");
    const unlockError = document.getElementById("unlockError");

    const balanceAmountEl = document.getElementById("balanceAmount");
    const daysLeftTextEl = document.getElementById("daysLeftText");
    const dailyBudgetEl = document.getElementById("dailyBudget");
    const totalBurnsCountEl = document.getElementById("totalBurnsCount");
    const totalOwedAmountEl = document.getElementById("totalOwedAmount");
    const owedBreakdownEl = document.getElementById("owedBreakdown");
    const runwayChartEl = document.getElementById("runwayChart");
    const liquidNavEl = document.getElementById("liquidNav");
    const liquidSpendableValEl = document.getElementById("liquidSpendableVal");
    const liquidCoreValEl = document.getElementById("liquidCoreVal");
    const deadCapitalWidget = document.getElementById("deadCapitalWidget");
    const deadCapitalVal = document.getElementById("deadCapitalVal");
    const deadCapitalBleed = document.getElementById("deadCapitalBleed");
    const syntheticNav = document.getElementById("syntheticNav");
    const syntheticAxis = document.getElementById("syntheticAxis");
    const syntheticGithub = document.getElementById("syntheticGithub");
    const apiBurnWidget = document.getElementById("apiBurnWidget");
    const apiRunwayDays = document.getElementById("apiRunwayDays");
    const apiBurnRate = document.getElementById("apiBurnRate");
    const resetBtn = document.getElementById("resetBtn");
    const logsFeed = document.getElementById("logsFeed");
    const bankSyncPill = document.getElementById("bankSyncPill");
    const categorySpendEl = document.getElementById("categorySpend");
    const learnedRulesEl = document.getElementById("learnedRules");
    const reviewListEl = document.getElementById("reviewList");
    const reviewCountEl = document.getElementById("reviewCount");

    const DEFAULT_BALANCE = 5000;
    const TARGET_DATE = new Date("2026-06-22T00:00:00");
    const TOKEN_KEY = "agent_cfo_access_token";
    const CATEGORIES = [
        "Food",
        "Transport",
        "Shopping",
        "Rent/Housing",
        "Utilities",
        "Subscriptions",
        "Business/Tools",
        "Travel",
        "Healthcare",
        "Education",
        "Income",
        "Transfer",
        "Refund",
        "Fees/Charges",
        "Unknown",
    ];

    let currentBalance = DEFAULT_BALANCE;
    let daysLeftFromServer = null;
    let transactions = [];
    let owedByState = {};
    let merchantRules = [];
    let refreshTimer = null;
    let syncTimer = null;

    const formatCurrency = (amount) => Number(amount || 0).toLocaleString("en-IN");
    const escapeHtml = (text) => {
        if (text == null) return "";
        const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
        return String(text).replace(/[&<>"']/g, (m) => map[m]);
    };
    const formatTime = (isoString) => {
        const date = new Date(isoString);
        if (Number.isNaN(date.getTime())) return "Unknown time";
        return date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) + " · " + date.toLocaleDateString("en-IN", { month: "short", day: "numeric" });
    };
    const getDaysRemaining = () => Math.max(Math.ceil((TARGET_DATE - new Date()) / (1000 * 60 * 60 * 24)), 1);
    const accessToken = () => sessionStorage.getItem(TOKEN_KEY) || "";

    const apiFetch = (url, options = {}) => {
        const headers = new Headers(options.headers || {});
        const token = accessToken();
        if (token) headers.set("X-CFO-Token", token);
        return fetch(url, { ...options, headers, credentials: "same-origin" });
    };

    const showPublic = async () => {
        privateApp.hidden = true;
        publicShell.hidden = false;
        try {
            const res = await fetch("/public-profile");
            if (!res.ok) return;
            const profile = await res.json();
            document.getElementById("publicTitle").textContent = profile.title || "AgentCFO";
            document.getElementById("publicTagline").textContent = profile.tagline || "";
            document.getElementById("publicPrivacy").textContent = profile.privacy_note || "";
            document.getElementById("publicActivity").innerHTML = (profile.public_activity || []).map((item) => `
                <div class="public-item">
                    <strong>${escapeHtml(item.label)}</strong>
                    <span>${escapeHtml(item.detail)}</span>
                </div>
            `).join("");
        } catch (err) {
            console.error("Public profile load failed:", err);
        }
    };

    const showPrivate = () => {
        publicShell.hidden = true;
        privateApp.hidden = false;
        if (!refreshTimer) refreshTimer = setInterval(() => fetchState(true), 30000);
        if (!syncTimer) syncTimer = setInterval(fetchBankSyncStatus, 60000);
    };

    const authenticate = async (token) => {
        const cleanToken = String(token || "").trim();
        if (!cleanToken) throw new Error("Enter the private access token.");
        const res = await fetch("/auth/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ token: cleanToken }),
        });
        if (!res.ok) throw new Error("Private access token was rejected.");
        sessionStorage.setItem(TOKEN_KEY, cleanToken);
    };

    const animateSpring = (startVal, endVal, onUpdate) => {
        let current = startVal;
        let velocity = 0;
        const tension = 120;
        const friction = 14;
        let lastTime = performance.now();
        const update = (currentTime) => {
            const dt = Math.min((currentTime - lastTime) / 1000, 0.032);
            lastTime = currentTime;
            const force = tension * (endVal - current) - friction * velocity;
            velocity += force * dt;
            current += velocity * dt;
            onUpdate(Math.round(current));
            if (Math.abs(velocity) < 0.5 && Math.abs(endVal - current) < 0.5) {
                onUpdate(endVal);
            } else {
                requestAnimationFrame(update);
            }
        };
        requestAnimationFrame(update);
    };

    const animateBalance = (startVal, endVal) => {
        animateSpring(startVal, endVal, (val) => {
            balanceAmountEl.textContent = formatCurrency(val);
        });
    };

    const extractPerson = (tx) => {
        if (!tx.owed_by_snapshot) return "";
        const names = Object.keys(tx.owed_by_snapshot);
        return names.length > 0 ? names[0] : "";
    };

    const getActionDisplay = (action, tx, balanceDelta) => {
        const person = extractPerson(tx);
        const personStr = person ? ` (@${person})` : "";
        const channel = tx.channel ? ` (${tx.channel})` : "";
        const category = tx.category && tx.category !== "Unknown" ? ` · ${tx.category}` : "";
        switch (action) {
            case "ADD_FUNDS": return { class: "badge-approved", text: `+₹${formatCurrency(Math.max(balanceDelta, 0))} Added`, icon: "↑" };
            case "DEBT_COLLECTED": return { class: "badge-approved", text: `+₹${formatCurrency(Math.max(balanceDelta, 0))} Collected${personStr}`, icon: "↑" };
            case "SET_EXACT_BALANCE": return { class: "badge-approved", text: `Override → ₹${formatCurrency(tx.remaining_balance)}`, icon: "≈" };
            case "LEND_MONEY": return { class: "badge-lend", text: `-₹${formatCurrency(tx.approved_amount)} Lent${personStr}`, icon: "→" };
            case "RETROACTIVE_DEDUCTION": return { class: "badge-rejected", text: `-₹${formatCurrency(tx.approved_amount)} Deducted`, icon: "↓" };
            case "APPROVE_INTENT": return { class: "badge-approved", text: `-₹${formatCurrency(tx.approved_amount)} Approved`, icon: "✓" };
            case "REJECT_INTENT": return { class: "badge-rejected", text: "Rejected (₹0)", icon: "✕" };
            case "QUERY_STATUS": return { class: "badge-query", text: "Status Query", icon: "ℹ" };
            case "SIMULATE_CONTRACT": return { class: "badge-approved", text: "Simulator", icon: "↔" };
            case "UPDATE_SYNTHETIC": return { class: "badge-bank-credit", text: "Asset Sync", icon: "◇" };
            case "REROUTE_TO_SYNTHETIC": return { class: "badge-rejected", text: "Intercepted", icon: "!" };
            case "BANK_DEBIT": return { class: "badge-bank-debit", text: `-₹${formatCurrency(tx.approved_amount)} Bank Debit${channel}${category}`, icon: "₹" };
            case "BANK_CREDIT": return { class: "badge-bank-credit", text: `+₹${formatCurrency(tx.funds_added)} Bank Credit${channel}${category}`, icon: "₹" };
            default: return { class: "badge-rejected", text: "Unknown", icon: "?" };
        }
    };

    const renderStats = () => {
        const daysLeft = daysLeftFromServer ?? getDaysRemaining();
        daysLeftTextEl.textContent = `${daysLeft} Days Left`;
        dailyBudgetEl.textContent = `₹${formatCurrency(Math.max(Math.round(currentBalance / daysLeft), 0))}/day`;
        totalBurnsCountEl.textContent = transactions.filter((t) => t.action_taken === "REJECT_INTENT").length;
        const totalOwed = Object.values(owedByState).reduce((acc, val) => acc + Number(val || 0), 0);
        totalOwedAmountEl.textContent = `₹${formatCurrency(totalOwed)}`;
        const entries = Object.entries(owedByState).filter(([, v]) => v > 0);
        owedBreakdownEl.innerHTML = entries.length === 0
            ? '<span class="owed-empty">No active ledgers</span>'
            : entries.map(([name, amt]) => `<span class="owed-person">@${escapeHtml(name)}: ₹${formatCurrency(amt)}</span>`).join("");
    };

    const renderLogs = () => {
        if (transactions.length === 0) {
            logsFeed.innerHTML = '<div class="empty-state"><span>System idle. Awaiting bank transactions.</span></div>';
            return;
        }
        logsFeed.innerHTML = "";
        const deltas = transactions.map((tx, idx) => {
            const prevBalance = (idx < transactions.length - 1) ? transactions[idx + 1].remaining_balance : DEFAULT_BALANCE;
            return tx.remaining_balance - prevBalance;
        });
        transactions.forEach((tx, idx) => {
            const display = getActionDisplay(tx.action_taken || "UNKNOWN", tx, deltas[idx]);
            const logDiv = document.createElement("div");
            logDiv.className = "log-entry";
            logDiv.style.animationDelay = `${idx * 0.03}s`;
            logDiv.innerHTML = `
                <div class="log-meta">
                    <span>${formatTime(tx.timestamp)}</span>
                    <span class="badge ${display.class}">${display.icon} ${display.text}</span>
                </div>
                <div class="log-intent">${tx.source === "GMAIL_SYNC" ? escapeHtml(tx.expense_text) : `"${escapeHtml(tx.expense_text)}"`}</div>
                <div class="log-response">
                    <span class="cfo-label">${tx.source === "GMAIL_SYNC" ? "BANK:" : "AGENT:"}</span>
                    <span class="cfo-text">${escapeHtml(tx.message)}</span>
                </div>
                <div class="log-footer">
                    <span class="person-tag">${escapeHtml(tx.classification_reason || "")}</span>
                    <span class="balance-after">Net Runway: ₹${formatCurrency(tx.remaining_balance)}</span>
                </div>
            `;
            logsFeed.appendChild(logDiv);
        });
    };

    const renderClassification = () => {
        const categoryTotals = {};
        transactions.forEach((tx) => {
            if (tx.action_taken !== "BANK_DEBIT") return;
            const category = tx.category || "Unknown";
            categoryTotals[category] = (categoryTotals[category] || 0) + Number(tx.approved_amount || 0);
        });
        const totals = Object.entries(categoryTotals).sort((a, b) => b[1] - a[1]);
        categorySpendEl.innerHTML = totals.length
            ? totals.map(([category, amount]) => `<div class="category-row"><span>${escapeHtml(category)}</span><span class="category-amount">₹${formatCurrency(amount)}</span></div>`).join("")
            : '<span class="empty-copy">No bank debits classified yet.</span>';

        const learned = merchantRules.filter((rule) => ["learned", "manual"].includes(rule.source));
        learnedRulesEl.innerHTML = learned.length
            ? learned.slice(0, 8).map((rule) => `
                <div class="rule-row">
                    <span>${escapeHtml(rule.pattern)}</span>
                    <span class="rule-meta">${escapeHtml(rule.category)} · ${Number(rule.usage_count || 0)} uses</span>
                </div>
            `).join("")
            : '<span class="empty-copy">No learned UPI vendors yet.</span>';

        const reviewTx = transactions.filter((tx) => tx.needs_review && tx.action_taken === "BANK_DEBIT");
        reviewCountEl.textContent = `${reviewTx.length} open`;
        reviewListEl.innerHTML = reviewTx.length
            ? reviewTx.map(renderReviewCard).join("")
            : '<div class="empty-state"><span>No ambiguous UPI payments right now.</span></div>';
    };

    const renderReviewCard = (tx) => {
        const selected = tx.category || "Unknown";
        const options = CATEGORIES.map((category) => `<option value="${category}" ${category === selected ? "selected" : ""}>${category}</option>`).join("");
        const payee = tx.payee_raw || tx.merchant || tx.upi_vpa || "Unknown payee";
        const meta = [
            `₹${formatCurrency(tx.approved_amount)}`,
            formatTime(tx.timestamp),
            tx.upi_vpa || tx.upi_handle || tx.channel || "UPI",
            `${Math.round(Number(tx.classification_confidence || 0) * 100)}%`,
        ].filter(Boolean).join(" · ");
        return `
            <div class="review-card" data-transaction-id="${escapeHtml(tx.id)}">
                <div>
                    <div class="review-merchant">${escapeHtml(payee)}</div>
                    <div class="review-meta">${escapeHtml(meta)}</div>
                    <div class="review-reason">${escapeHtml(tx.classification_reason || "Needs review.")}</div>
                </div>
                <select aria-label="Category">${options}</select>
                <button class="btn btn-primary review-save" type="button">Save</button>
            </div>
        `;
    };

    reviewListEl.addEventListener("click", async (event) => {
        const button = event.target.closest(".review-save");
        if (!button) return;
        const card = button.closest(".review-card");
        const transactionId = card.dataset.transactionId;
        const category = card.querySelector("select").value;
        button.disabled = true;
        button.textContent = "Saving";
        try {
            const res = await apiFetch(`/transactions/${encodeURIComponent(transactionId)}/classify`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ category, subcategory: "", always_apply: true }),
            });
            if (!res.ok) throw new Error("Classification update failed");
            await fetchState(false);
        } catch (err) {
            alert(err.message);
            button.disabled = false;
            button.textContent = "Save";
        }
    });

    const drawRunwayChart = () => {
        const svgWidth = 600, svgHeight = 80;
        const pointsBase = [DEFAULT_BALANCE, ...[...transactions].reverse().map((tx) => tx.remaining_balance)];
        if (pointsBase[pointsBase.length - 1] !== currentBalance) pointsBase.push(currentBalance);
        const maxBalance = Math.max(DEFAULT_BALANCE, ...pointsBase);
        const stepX = svgWidth / (Math.max(pointsBase.length, 3) - 1);
        const points = pointsBase.map((val, idx) => ({
            x: idx * stepX,
            y: 10 + (1 - Math.max(val / maxBalance, 0)) * 60,
        }));
        runwayChartEl.innerHTML = `
            <defs>
                <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="var(--color-indigo)" stop-opacity="0.3"></stop>
                    <stop offset="100%" stop-color="var(--color-indigo)" stop-opacity="0.0"></stop>
                </linearGradient>
            </defs>`;
        if (!points.length) return;
        const dLine = points.map((p, idx) => `${idx === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
        const dArea = `M ${points[0].x} ${svgHeight} L ${points.map((p) => `${p.x} ${p.y}`).join(" L ")} L ${points[points.length - 1].x} ${svgHeight} Z`;
        const areaPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
        areaPath.setAttribute("fill", "url(#chartGrad)");
        areaPath.setAttribute("d", dArea);
        runwayChartEl.appendChild(areaPath);
        const linePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
        linePath.setAttribute("class", "chart-line");
        linePath.setAttribute("d", dLine);
        runwayChartEl.appendChild(linePath);
    };

    const fetchState = async (animate = false) => {
        try {
            const res = await apiFetch("/cfo-state");
            if (res.status === 401 || res.status === 503) {
                await showPublic();
                return false;
            }
            if (!res.ok) throw new Error("Failed to sync agent state");
            const state = await res.json();
            const prevBalance = currentBalance;
            currentBalance = state.current_balance;
            daysLeftFromServer = Number.isFinite(state.days_left) ? state.days_left : null;
            transactions = state.transactions || [];
            owedByState = state.owed_by || {};
            merchantRules = state.merchant_rules || [];

            if (state.liquid_metrics) {
                liquidNavEl.textContent = `₹${formatCurrency(state.liquid_metrics.nav)}`;
                liquidSpendableValEl.textContent = `₹${formatCurrency(state.liquid_metrics.spendable)}`;
                liquidCoreValEl.textContent = `₹${formatCurrency(state.liquid_metrics.core)}`;
            }
            if (state.dead_capital_opportunity_cost && currentBalance > 2000) {
                deadCapitalWidget.style.display = "block";
                deadCapitalVal.textContent = `₹${formatCurrency(currentBalance)}`;
                deadCapitalBleed.textContent = `₹${state.dead_capital_opportunity_cost}/day`;
            } else {
                deadCapitalWidget.style.display = "none";
            }
            if (state.synthetic_metrics && state.synthetic_assets) {
                syntheticNav.textContent = `₹${formatCurrency(state.synthetic_metrics.total_cash_equivalent)}`;
                syntheticAxis.textContent = `${state.synthetic_assets.axis_edge_points || 0} pts`;
                syntheticGithub.textContent = `$${formatCurrency(state.synthetic_assets.github_credits_usd || 0)}`;
            }
            if (state.gcp_billing) {
                const daysLeft = state.gcp_billing.vertex_runway_days;
                if (daysLeft >= 0 && daysLeft < 30) {
                    apiBurnWidget.style.display = "block";
                    apiRunwayDays.textContent = `${daysLeft} days`;
                    apiBurnRate.textContent = `$${state.gcp_billing.vertex_daily_burn_usd}/day`;
                } else {
                    apiBurnWidget.style.display = "none";
                }
            }

            showPrivate();
            renderStats();
            renderLogs();
            renderClassification();
            drawRunwayChart();
            if (animate) animateBalance(prevBalance, currentBalance);
            else balanceAmountEl.textContent = formatCurrency(currentBalance);
            return true;
        } catch (err) {
            console.error("Agent Sync Error:", err);
            await showPublic();
            return false;
        }
    };

    const fetchBankSyncStatus = async () => {
        try {
            const res = await apiFetch("/gmail-status");
            if (!res.ok) return;
            const status = await res.json();
            if (status.watch_active && status.last_sync_at) {
                const ago = Math.round((Date.now() - new Date(status.last_sync_at).getTime()) / 60000);
                const agoText = ago < 1 ? "Just now" : ago < 60 ? `${ago}m ago` : `${Math.round(ago / 60)}h ago`;
                bankSyncPill.textContent = `₹ ${agoText}`;
                bankSyncPill.title = `Bank Sync Active — Last: ${agoText} — Total: ${status.total_synced} synced`;
                bankSyncPill.classList.add("active");
            } else {
                bankSyncPill.textContent = "Bank Sync Inactive";
                bankSyncPill.title = "Bank sync not configured";
                bankSyncPill.classList.remove("active");
            }
        } catch (e) {
            bankSyncPill.textContent = "Bank Sync Locked";
        }
    };

    resetBtn.addEventListener("click", async () => {
        if (!confirm("Wipe entire ledger and reset to ₹5,000?")) return;
        try {
            const res = await apiFetch("/cfo-reset", { method: "POST" });
            if (!res.ok) throw new Error("Failed to reset ledger");
            await fetchState(true);
        } catch (err) {
            alert(`Reset Error: ${err.message}`);
        }
    });

    unlockForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        unlockError.hidden = true;
        try {
            await authenticate(unlockToken.value);
            unlockToken.value = "";
            await fetchState(false);
            await fetchBankSyncStatus();
        } catch (err) {
            unlockError.textContent = err.message;
            unlockError.hidden = false;
        }
    });

    const boot = async () => {
        const url = new URL(window.location.href);
        const tokenFromUrl = url.searchParams.get("token");
        if (tokenFromUrl) {
            try {
                await authenticate(tokenFromUrl);
            } catch (err) {
                console.warn(err);
            }
            url.searchParams.delete("token");
            window.history.replaceState({}, "", url.toString());
        }
        const ok = await fetchState(false);
        if (ok) await fetchBankSyncStatus();
        else await showPublic();
    };

    boot();
});
