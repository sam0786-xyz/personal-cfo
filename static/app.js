document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements
    const balanceAmountEl = document.getElementById("balanceAmount");
    const daysLeftTextEl = document.getElementById("daysLeftText");
    const dailyBudgetEl = document.getElementById("dailyBudget");
    const totalBurnsCountEl = document.getElementById("totalBurnsCount");
    const totalOwedAmountEl = document.getElementById("totalOwedAmount");
    const owedBreakdownEl = document.getElementById("owedBreakdown");
    const runwayChartEl = document.getElementById("runwayChart");
    
    // Liquid Assets DOM Elements
    const liquidNavEl = document.getElementById("liquidNav");
    const liquidSpendableValEl = document.getElementById("liquidSpendableVal");
    const liquidCoreValEl = document.getElementById("liquidCoreVal");
    
    // Dead Capital DOM
    const deadCapitalWidget = document.getElementById("deadCapitalWidget");
    const deadCapitalVal = document.getElementById("deadCapitalVal");
    const deadCapitalBleed = document.getElementById("deadCapitalBleed");

    // Synthetic Assets DOM
    const syntheticNav = document.getElementById("syntheticNav");
    const syntheticAxis = document.getElementById("syntheticAxis");
    const syntheticGithub = document.getElementById("syntheticGithub");

    // API Burn DOM
    const apiBurnWidget = document.getElementById("apiBurnWidget");
    const apiRunwayDays = document.getElementById("apiRunwayDays");
    const apiBurnRate = document.getElementById("apiBurnRate");
    
    const resetBtn = document.getElementById("resetBtn");
    const logsFeed = document.getElementById("logsFeed");

    // CFO State Variables
    let currentBalance = 5000;
    const DEFAULT_BALANCE = 5000;
    let transactions = [];
    let owedByState = {};
    
    // Config Target Date
    const TARGET_DATE = new Date("2026-06-22T00:00:00");

    // Helpers
    const formatCurrency = (amount) => Number(amount).toLocaleString('en-IN');
    const escapeHtml = (text) => {
        if (text == null) return '';
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
        return String(text).replace(/[&<>"']/g, m => map[m]);
    };
    const formatTime = (isoString) => {
        const date = new Date(isoString);
        return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) + ' · ' + date.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });
    };
    const getDaysRemaining = () => Math.max(Math.ceil((TARGET_DATE - new Date()) / (1000 * 60 * 60 * 24)), 1);

    // Advanced Spring Physics (Emil Kowalski style)
    const animateSpring = (startVal, endVal, onUpdate) => {
        let current = startVal;
        let velocity = 0;
        const tension = 120;
        const friction = 14;
        const mass = 1;
        let lastTime = performance.now();

        const update = (currentTime) => {
            const dt = Math.min((currentTime - lastTime) / 1000, 0.032);
            lastTime = currentTime;

            const force = tension * (endVal - current) - friction * velocity;
            velocity += (force / mass) * dt;
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

    // Action Display Mapper
    const getActionDisplay = (action, tx, balanceDelta) => {
        const person = extractPerson(tx);
        const personStr = person ? ` (@${person})` : '';
        const channel = tx.channel ? ` (${tx.channel})` : '';

        switch (action) {
            case "ADD_FUNDS": return { class: "badge-approved", text: `+₹${formatCurrency(Math.max(balanceDelta, 0))} Added`, icon: "↑" };
            case "DEBT_COLLECTED": return { class: "badge-approved", text: `+₹${formatCurrency(Math.max(balanceDelta, 0))} Collected${personStr}`, icon: "↑" };
            case "SET_EXACT_BALANCE": return { class: "badge-approved", text: `Override → ₹${formatCurrency(tx.remaining_balance)}`, icon: "≈" };
            case "LEND_MONEY": return { class: "badge-lend", text: `-₹${formatCurrency(tx.approved_amount)} Lent${personStr}`, icon: "→" };
            case "RETROACTIVE_DEDUCTION": return { class: "badge-rejected", text: `-₹${formatCurrency(tx.approved_amount)} Deducted`, icon: "↓" };
            case "APPROVE_INTENT": return { class: "badge-approved", text: `-₹${formatCurrency(tx.approved_amount)} Approved`, icon: "✓" };
            case "REJECT_INTENT": return { class: "badge-rejected", text: "Rejected (₹0)", icon: "✕" };
            case "QUERY_STATUS": return { class: "badge-query", text: "Status Query", icon: "ℹ" };
            case "SIMULATE_CONTRACT": return { class: "badge-approved", text: `Simulator`, icon: "🌍" };
            case "UPDATE_SYNTHETIC": return { class: "badge-bank-credit", text: `Asset Sync`, icon: "✨" };
            case "REROUTE_TO_SYNTHETIC": return { class: "badge-rejected", text: `Intercepted`, icon: "🛑" };
            case "BANK_DEBIT": return { class: "badge-bank-debit", text: `-₹${formatCurrency(tx.approved_amount)} Bank Debit${channel}`, icon: "🏦" };
            case "BANK_CREDIT": return { class: "badge-bank-credit", text: `+₹${formatCurrency(tx.funds_added)} Bank Credit${channel}`, icon: "🏦" };
            default: return { class: "badge-rejected", text: `Unknown`, icon: "?" };
        }
    };

    const extractPerson = (tx) => {
        if (!tx.owed_by_snapshot) return "";
        const names = Object.keys(tx.owed_by_snapshot);
        return names.length > 0 ? names[0] : "";
    };

    // Render Stats
    const renderStats = () => {
        const daysLeft = getDaysRemaining();
        daysLeftTextEl.textContent = `${daysLeft} Days Left`;
        
        const budgetVal = Math.max(Math.round(currentBalance / daysLeft), 0);
        dailyBudgetEl.textContent = `₹${formatCurrency(budgetVal)}/day`;
        
        const totalBurns = transactions.filter(t => t.action_taken === "REJECT_INTENT").length;
        totalBurnsCountEl.textContent = totalBurns;

        const totalOwed = Object.values(owedByState).reduce((acc, val) => acc + val, 0);
        totalOwedAmountEl.textContent = `₹${formatCurrency(totalOwed)}`;

        const entries = Object.entries(owedByState).filter(([, v]) => v > 0);
        if (entries.length === 0) {
            owedBreakdownEl.innerHTML = '<span class="owed-empty">No active ledgers</span>';
        } else {
            owedBreakdownEl.innerHTML = entries.map(([name, amt]) => 
                `<span class="owed-person">@${escapeHtml(name)}: ₹${formatCurrency(amt)}</span>`
            ).join('');
        }
    };

    // Render Logs (Audit Feed)
    const renderLogs = () => {
        if (transactions.length === 0) {
            logsFeed.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="12" cy="12" r="10"></circle>
                        <path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01" />
                    </svg>
                    <span>System idle. Awaiting bank transactions.</span>
                </div>`;
            return;
        }

        logsFeed.innerHTML = "";
        const deltas = transactions.map((tx, idx) => {
            const prevBalance = (idx < transactions.length - 1) ? transactions[idx + 1].remaining_balance : DEFAULT_BALANCE;
            return tx.remaining_balance - prevBalance;
        });

        transactions.forEach((tx, idx) => {
            const action = tx.action_taken || "UNKNOWN";
            const delta = deltas[idx];
            const display = getActionDisplay(action, tx, delta);

            const logDiv = document.createElement("div");
            logDiv.className = "log-entry";
            logDiv.style.animationDelay = `${idx * 0.05}s`;

            logDiv.innerHTML = `
                <div class="log-meta">
                    <span>${formatTime(tx.timestamp)}</span>
                    <span class="badge ${display.class}">${display.icon} ${display.text}</span>
                </div>
                <div class="log-intent">${tx.source === 'GMAIL_SYNC' ? tx.expense_text : '"' + escapeHtml(tx.expense_text) + '"'}</div>
                <div class="log-response">
                    <span class="cfo-label">${tx.source === 'GMAIL_SYNC' ? 'BANK:' : 'AGENT:'}</span>
                    <span class="cfo-text">${escapeHtml(tx.message)}</span>
                </div>
                <div class="log-footer">
                    <span></span>
                    <span class="balance-after">Net Runway: ₹${formatCurrency(tx.remaining_balance)}</span>
                </div>
            `;
            logsFeed.appendChild(logDiv);
        });
    };

    // SVG Chart Drawing (Sleek Sparkline)
    const drawRunwayChart = () => {
        const svgWidth = 600, svgHeight = 80;
        const paddingLeft = 0, paddingRight = 0, paddingTop = 10, paddingBottom = 10;
        const chartUsableWidth = svgWidth - paddingLeft - paddingRight;
        const chartUsableHeight = svgHeight - paddingTop - paddingBottom;

        runwayChartEl.innerHTML = `
            <defs>
                <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="var(--color-indigo)" stop-opacity="0.3"></stop>
                    <stop offset="100%" stop-color="var(--color-indigo)" stop-opacity="0.0"></stop>
                </linearGradient>
                <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                    <feGaussianBlur stdDeviation="3" result="blur" />
                    <feComposite in="SourceGraphic" in2="blur" operator="over" />
                </filter>
            </defs>`;

        let balancePoints = [DEFAULT_BALANCE];
        const chronTx = [...transactions].reverse();
        chronTx.forEach(tx => balancePoints.push(tx.remaining_balance));
        if (balancePoints[balancePoints.length - 1] !== currentBalance) balancePoints.push(currentBalance);

        const maxBalance = Math.max(DEFAULT_BALANCE, ...balancePoints);
        const maxSlots = Math.max(balancePoints.length, 3);
        const stepX = chartUsableWidth / (maxSlots - 1);

        let points = balancePoints.map((val, idx) => ({
            x: paddingLeft + idx * stepX,
            y: paddingTop + (1 - Math.max(val / maxBalance, 0)) * chartUsableHeight
        }));

        if (points.length > 20) {
            const step = Math.ceil(points.length / 20);
            const sampled = [points[0]];
            for (let i = step; i < points.length - 1; i += step) sampled.push(points[i]);
            sampled.push(points[points.length - 1]);
            const newStepX = chartUsableWidth / (sampled.length - 1);
            sampled.forEach((p, idx) => { p.x = paddingLeft + idx * newStepX; });
            points = sampled;
        }

        if (points.length > 0) {
            const lastPoint = points[points.length - 1];
            const endX = paddingLeft + chartUsableWidth;
            if (lastPoint.x < endX - 5) {
                const projLine = document.createElementNS("http://www.w3.org/2000/svg", "path");
                projLine.setAttribute("class", "chart-projection");
                projLine.setAttribute("d", `M ${lastPoint.x} ${lastPoint.y} L ${endX} ${lastPoint.y}`);
                runwayChartEl.appendChild(projLine);
            }

            let dLine = `M ${points[0].x} ${points[0].y}`;
            let dArea = `M ${points[0].x} ${svgHeight} L ${points[0].x} ${points[0].y}`;
            
            for (let i = 1; i < points.length; i++) {
                dLine += ` L ${points[i].x} ${points[i].y}`;
                dArea += ` L ${points[i].x} ${points[i].y}`;
            }
            dArea += ` L ${points[points.length - 1].x} ${svgHeight} Z`;

            const areaPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
            areaPath.setAttribute("fill", "url(#chartGrad)");
            areaPath.setAttribute("d", dArea);
            runwayChartEl.appendChild(areaPath);

            const linePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
            linePath.setAttribute("class", "chart-line");
            linePath.setAttribute("d", dLine);
            runwayChartEl.appendChild(linePath);

            const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
            dot.setAttribute("class", "chart-dot");
            dot.setAttribute("cx", lastPoint.x);
            dot.setAttribute("cy", lastPoint.y);
            dot.setAttribute("r", 4);
            runwayChartEl.appendChild(dot);
        }
    };

    // State Synchronization
    const fetchState = async (animate = false) => {
        try {
            const res = await fetch("/cfo-state");
            if (!res.ok) throw new Error("Failed to sync agent state");
            
            const state = await res.json();
            const prevBalance = currentBalance;
            currentBalance = state.current_balance;
            transactions = state.transactions || [];
            owedByState = state.owed_by || {};

            // Update Liquid Assets
            if (state.liquid_metrics) {
                liquidNavEl.textContent = `₹${formatCurrency(state.liquid_metrics.nav)}`;
                liquidSpendableValEl.textContent = `₹${formatCurrency(state.liquid_metrics.spendable)}`;
                liquidCoreValEl.textContent = `₹${formatCurrency(state.liquid_metrics.core)}`;
            }

            // Update Dead Capital Radar
            if (state.dead_capital_opportunity_cost && currentBalance > 2000) {
                deadCapitalWidget.style.display = "block";
                deadCapitalVal.textContent = `₹${formatCurrency(currentBalance)}`;
                deadCapitalBleed.textContent = `₹${state.dead_capital_opportunity_cost}/day`;
            } else {
                deadCapitalWidget.style.display = "none";
            }

            // Update Synthetic Assets
            if (state.synthetic_metrics && state.synthetic_assets) {
                syntheticNav.textContent = `₹${formatCurrency(state.synthetic_metrics.total_cash_equivalent)}`;
                syntheticAxis.textContent = `${state.synthetic_assets.axis_edge_points || 0} pts`;
                syntheticGithub.textContent = `$${formatCurrency(state.synthetic_assets.github_credits_usd || 0)}`;
            }

            // Update API Burn Radar
            if (state.gcp_billing) {
                const daysLeft = state.gcp_billing.vertex_runway_days;
                if (daysLeft >= 0 && daysLeft < 30) {
                    apiBurnWidget.style.display = "block";
                    apiRunwayDays.textContent = `${daysLeft} days`;
                    apiBurnRate.textContent = `$${state.gcp_billing.vertex_daily_burn_usd}/day`;
                } else {
                    apiBurnWidget.style.display = "none";
                }
            } else {
                apiBurnWidget.style.display = "none";
            }

            renderStats();
            renderLogs();
            drawRunwayChart();

            if (animate) animateBalance(prevBalance, currentBalance);
            else balanceAmountEl.textContent = formatCurrency(currentBalance);

        } catch (err) {
            console.error("Agent Sync Error:", err);
        }
    };

    // Reset Logic
    resetBtn.addEventListener("click", async () => {
        if (!confirm("Wipe entire ledger and reset to ₹5,000?")) return;
        try {
            const res = await fetch("/cfo-reset", { method: "POST" });
            if (!res.ok) throw new Error("Failed to reset ledger");
            await fetchState(true);
        } catch (err) {
            alert(`Reset Error: ${err.message}`);
        }
    });

    // Bank Sync Status
    const bankSyncPill = document.getElementById('bankSyncPill');
    const fetchBankSyncStatus = async () => {
        try {
            const res = await fetch('/gmail-status');
            if (!res.ok) return;
            const status = await res.json();
            if (bankSyncPill) {
                if (status.watch_active && status.last_sync_at) {
                    const ago = Math.round((Date.now() - new Date(status.last_sync_at).getTime()) / 60000);
                    const agoText = ago < 1 ? 'Just now' : ago < 60 ? `${ago}m ago` : `${Math.round(ago/60)}h ago`;
                    bankSyncPill.textContent = `🏦 ${agoText}`;
                    bankSyncPill.title = `Bank Sync Active — Last: ${agoText} — Total: ${status.total_synced} synced`;
                    bankSyncPill.classList.add('active');
                } else {
                    bankSyncPill.textContent = '🏦 Inactive';
                    bankSyncPill.title = 'Bank sync not configured';
                    bankSyncPill.classList.remove('active');
                }
            }
        } catch (e) { /* silent fail */ }
    };

    // Boot
    fetchState(false);
    fetchBankSyncStatus();
    // Auto-refresh state every 30 seconds to pick up new bank syncs
    setInterval(() => fetchState(true), 30000);
    // Refresh bank sync status every 60 seconds
    setInterval(fetchBankSyncStatus, 60000);
});
