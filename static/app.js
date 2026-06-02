document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements
    const balanceAmountEl = document.getElementById("balanceAmount");
    const daysLeftTextEl = document.getElementById("daysLeftText");
    const dailyBudgetEl = document.getElementById("dailyBudget");
    const totalBurnsCountEl = document.getElementById("totalBurnsCount");
    const totalOwedAmountEl = document.getElementById("totalOwedAmount");
    const owedBreakdownEl = document.getElementById("owedBreakdown");
    const runwayChartEl = document.getElementById("runwayChart");
    
    const expenseForm = document.getElementById("expenseForm");
    const expenseInput = document.getElementById("expenseInput");
    const submitBtn = document.getElementById("submitBtn");
    const resetBtn = document.getElementById("resetBtn");
    
    const logsFeed = document.getElementById("logsFeed");
    const thinkingLoader = document.getElementById("thinkingLoader");
    const loaderTitle = document.getElementById("loaderTitle");
    const loaderSubtitle = document.getElementById("loaderSubtitle");

    // CFO State Variables
    let currentBalance = 5000;
    const DEFAULT_BALANCE = 5000;
    let transactions = [];
    let owedByState = {};
    
    // Config Target Date
    const TARGET_DATE = new Date("2026-06-22T00:00:00");

    // Loader Roast Scripts
    const loadingRoasts = [
        { title: "Analyzing intent...", subtitle: "Simulating runway collapse sequence" },
        { title: "Cross-referencing metrics...", subtitle: "Evaluating standard young-adult behavior" },
        { title: "Calculating decay...", subtitle: "Analyzing percentage of total net worth" },
        { title: "Consulting models...", subtitle: "Translating disappointment into terminal output" },
        { title: "Validating deduction...", subtitle: "Waiting for blockchain confirmation (just kidding)" },
        { title: "Auditing ledger...", subtitle: "Checking who owes you money" }
    ];
    let loaderInterval = null;

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

    // Advanced Easing: expoOut
    const easeOutExpo = (x) => x === 1 ? 1 : 1 - Math.pow(2, -10 * x);

    const animateBalance = (startVal, endVal, duration = 1500) => {
        const start = performance.now();
        function update(currentTime) {
            const elapsed = currentTime - start;
            const progress = Math.min(elapsed / duration, 1);
            const easeProgress = easeOutExpo(progress);
            
            const currentVal = Math.round(startVal + (endVal - startVal) * easeProgress);
            balanceAmountEl.textContent = formatCurrency(currentVal);
            
            if (progress < 1) requestAnimationFrame(update);
            else balanceAmountEl.textContent = formatCurrency(endVal);
        }
        requestAnimationFrame(update);
    };

    // Action Display Mapper
    const getActionDisplay = (action, tx, balanceDelta) => {
        const person = extractPerson(tx);
        const personStr = person ? ` (@${person})` : '';

        switch (action) {
            case "ADD_FUNDS": return { class: "badge-approved", text: `+₹${formatCurrency(Math.max(balanceDelta, 0))} Added`, icon: "↑" };
            case "DEBT_COLLECTED": return { class: "badge-approved", text: `+₹${formatCurrency(Math.max(balanceDelta, 0))} Collected${personStr}`, icon: "↑" };
            case "SET_EXACT_BALANCE": return { class: "badge-approved", text: `Override → ₹${formatCurrency(tx.remaining_balance)}`, icon: "≈" };
            case "LEND_MONEY": return { class: "badge-lend", text: `-₹${formatCurrency(tx.approved_amount)} Lent${personStr}`, icon: "→" };
            case "RETROACTIVE_DEDUCTION": return { class: "badge-rejected", text: `-₹${formatCurrency(tx.approved_amount)} Deducted`, icon: "↓" };
            case "APPROVE_INTENT": return { class: "badge-approved", text: `-₹${formatCurrency(tx.approved_amount)} Approved`, icon: "✓" };
            case "REJECT_INTENT": return { class: "badge-rejected", text: "Rejected (₹0)", icon: "✕" };
            case "QUERY_STATUS": return { class: "badge-query", text: "Status Query", icon: "ℹ" };
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
                    <span>System idle. Awaiting financial intent.</span>
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
                <div class="log-intent">"${escapeHtml(tx.expense_text)}"</div>
                <div class="log-response">
                    <span class="cfo-label">AGENT:</span>
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

            renderStats();
            renderLogs();
            drawRunwayChart();

            if (animate) animateBalance(prevBalance, currentBalance);
            else balanceAmountEl.textContent = formatCurrency(currentBalance);

        } catch (err) {
            console.error("Agent Sync Error:", err);
        }
    };

    // Submit Logic
    expenseForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const text = expenseInput.value.trim();
        if (!text) return;

        thinkingLoader.classList.remove("hidden");
        let roastIdx = 0;
        loaderTitle.textContent = loadingRoasts[0].title;
        loaderSubtitle.textContent = loadingRoasts[0].subtitle;
        loaderInterval = setInterval(() => {
            roastIdx = (roastIdx + 1) % loadingRoasts.length;
            loaderTitle.textContent = loadingRoasts[roastIdx].title;
            loaderSubtitle.textContent = loadingRoasts[roastIdx].subtitle;
        }, 1200);

        try {
            const res = await fetch("/cfo-check", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ expense_text: text })
            });

            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail || "Agent evaluation failed");
            }

            expenseInput.value = "";
            await fetchState(true);
        } catch (err) {
            alert(`Execution Error: ${err.message}`);
        } finally {
            clearInterval(loaderInterval);
            thinkingLoader.classList.add("hidden");
        }
    });

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

    // Shortcuts
    document.querySelectorAll(".shortcut-tag").forEach(tag => {
        tag.addEventListener("click", () => {
            expenseInput.value = tag.getAttribute("data-value");
            expenseInput.focus();
        });
    });

    // Boot
    fetchState(false);
});
