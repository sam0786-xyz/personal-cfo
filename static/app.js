document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements
    const balanceAmountEl = document.getElementById("balanceAmount");
    const daysLeftTextEl = document.getElementById("daysLeftText");
    const dailyBudgetEl = document.getElementById("dailyBudget");
    const totalBurnsCountEl = document.getElementById("totalBurnsCount");
    const totalOwedAmountEl = document.getElementById("totalOwedAmount");
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
    let transactions = [];
    let owedByState = {};
    
    // Config Target Date
    const TARGET_DATE = new Date("2026-06-22T00:00:00");

    // Funny loader roast scripts
    const loadingRoasts = [
        { title: "Analyzing poor life choices...", subtitle: "Simulating runway collapse sequence" },
        { title: "Cross-referencing ROI metrics...", subtitle: "Evaluating standard young-adult behavior" },
        { title: "Harshly calculating runway decay...", subtitle: "Analyzing percentage of total net worth" },
        { title: "Consulting financial models...", subtitle: "Translating deep disappointment into sarcasm" },
        { title: "Preparing vocal burn...", subtitle: "Deduction validation in progress" },
        { title: "Auditing lending history...", subtitle: "Checking who still owes you money" }
    ];
    let loaderInterval = null;

    // Helper: Format large numbers with comma (Indian locale)
    const formatCurrency = (amount) => {
        return Number(amount).toLocaleString('en-IN');
    };

    // Helper: Get days remaining
    const getDaysRemaining = () => {
        const today = new Date();
        const diffTime = TARGET_DATE - today;
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
        return Math.max(diffDays, 1);
    };

    // Helper: Formatting timestamp
    const formatTime = (isoString) => {
        const date = new Date(isoString);
        return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) + ' - ' + date.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });
    };

    // Escape HTML helper to prevent XSS injection — guards against null/undefined
    const escapeHtml = (text) => {
        if (text == null) return '';
        const str = String(text);
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return str.replace(/[&<>"']/g, function(m) { return map[m]; });
    };

    // Count-Up Animation for Balance Display
    const animateBalance = (startVal, endVal, duration = 1200) => {
        const start = performance.now();
        
        function update(currentTime) {
            const elapsed = currentTime - start;
            const progress = Math.min(elapsed / duration, 1);
            
            // Ease Out Expo animation curve
            const easeProgress = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
            
            const currentVal = Math.round(startVal + (endVal - startVal) * easeProgress);
            balanceAmountEl.textContent = formatCurrency(currentVal);
            
            if (progress < 1) {
                requestAnimationFrame(update);
            } else {
                balanceAmountEl.textContent = formatCurrency(endVal);
            }
        }
        
        requestAnimationFrame(update);
    };

    // Fetch and sync application state from backend
    const fetchState = async (animate = false) => {
        try {
            const res = await fetch("/cfo-state");
            if (!res.ok) throw new Error("Failed to load CFO state");
            
            const state = await res.json();
            const prevBalance = currentBalance;
            currentBalance = state.current_balance;
            transactions = state.transactions || [];
            owedByState = state.owed_by || {};

            // Render stats
            const daysLeft = getDaysRemaining();
            daysLeftTextEl.textContent = `${daysLeft} Days Left`;
            
            const budgetVal = Math.max(Math.round(currentBalance / daysLeft), 0);
            dailyBudgetEl.textContent = `₹${formatCurrency(budgetVal)}/day`;
            
            // Burns Count: REJECT_INTENT actions
            const totalBurns = transactions.filter(t => t.action_taken === "REJECT_INTENT").length;
            totalBurnsCountEl.textContent = totalBurns;

            // Calculate total owed from the live owed_by ledger
            const totalOwed = Object.values(owedByState).reduce((acc, val) => acc + val, 0);
            if (totalOwedAmountEl) {
                totalOwedAmountEl.textContent = `₹${formatCurrency(totalOwed)}`;
            }

            // Animate or set balance
            if (animate) {
                animateBalance(prevBalance, currentBalance);
            } else {
                balanceAmountEl.textContent = formatCurrency(currentBalance);
            }

            renderOwedBreakdown();
            renderLogs();
            drawRunwayChart();

        } catch (err) {
            console.error("Error synchronizing state:", err);
        }
    };

    // Render per-person debt breakdown under the "Money Owed" stat
    const renderOwedBreakdown = () => {
        const container = document.getElementById("owedBreakdown");
        if (!container) return;

        const entries = Object.entries(owedByState).filter(([, v]) => v > 0);
        if (entries.length === 0) {
            container.innerHTML = '<span class="owed-empty">No outstanding debts</span>';
            return;
        }

        container.innerHTML = entries.map(([name, amt]) => 
            `<span class="owed-person">${escapeHtml(name)}: ₹${formatCurrency(amt)}</span>`
        ).join('');
    };

    // ============================================================
    // ACTION → VISUAL MAPPING (matches main.py's 8 action types)
    // ============================================================
    //
    // main.py stores per transaction:
    //   { timestamp, expense_text, action_taken, approved_amount, message, remaining_balance, owed_by_snapshot }
    //
    // "approved_amount" is always the EXPENSE deducted (0 for income/query actions).
    // There is NO "funds_added" field in the stored transaction.
    // To compute income amounts, we diff remaining_balance with the previous transaction.
    //
    // Action mapping:
    //   ADD_FUNDS          → income, approved_amount=0, balance went UP
    //   DEBT_COLLECTED     → income, approved_amount=0, balance went UP, person in owed_by_snapshot
    //   SET_EXACT_BALANCE  → override, approved_amount=0, balance changed
    //   LEND_MONEY         → deduction, approved_amount=X, person in owed_by_snapshot
    //   RETROACTIVE_DEDUCTION → deduction, approved_amount=X
    //   APPROVE_INTENT     → deduction, approved_amount=X
    //   REJECT_INTENT      → no change, approved_amount=0
    //   QUERY_STATUS        → no change, approved_amount=0

    const getActionDisplay = (action, tx, balanceDelta) => {
        switch (action) {
            case "ADD_FUNDS":
                return {
                    cardClass: "approved-card",
                    chipClass: "chip-approved",
                    chipText: `+₹${formatCurrency(balanceDelta > 0 ? balanceDelta : 0)} Added`,
                    icon: "💰"
                };
            case "DEBT_COLLECTED": {
                const person = extractPerson(tx);
                return {
                    cardClass: "approved-card",
                    chipClass: "chip-approved",
                    chipText: `+₹${formatCurrency(balanceDelta > 0 ? balanceDelta : 0)} Collected`,
                    icon: "🤝",
                    personLabel: person ? `from ${person}` : ""
                };
            }
            case "SET_EXACT_BALANCE":
                return {
                    cardClass: "approved-card",
                    chipClass: "chip-approved",
                    chipText: `Balance → ₹${formatCurrency(tx.remaining_balance)}`,
                    icon: "⚡"
                };
            case "LEND_MONEY": {
                const person = extractPerson(tx);
                return {
                    cardClass: "lend-card",
                    chipClass: "chip-lend",
                    chipText: `-₹${formatCurrency(tx.approved_amount)} Lent`,
                    icon: "🤲",
                    personLabel: person ? `to ${person}` : ""
                };
            }
            case "RETROACTIVE_DEDUCTION":
                return {
                    cardClass: "rejected-card",
                    chipClass: "chip-rejected",
                    chipText: `-₹${formatCurrency(tx.approved_amount)} Deducted`,
                    icon: "🔥"
                };
            case "APPROVE_INTENT":
                return {
                    cardClass: "approved-card",
                    chipClass: "chip-approved",
                    chipText: `-₹${formatCurrency(tx.approved_amount)} Approved`,
                    icon: "✅"
                };
            case "REJECT_INTENT":
                return {
                    cardClass: "rejected-card",
                    chipClass: "chip-rejected",
                    chipText: "Rejected (₹0)",
                    icon: "🚫"
                };
            case "QUERY_STATUS":
                return {
                    cardClass: "query-card",
                    chipClass: "chip-query",
                    chipText: "Status Query",
                    icon: "📊"
                };
            default:
                return {
                    cardClass: "rejected-card",
                    chipClass: "chip-rejected",
                    chipText: tx.approved_amount > 0 ? `-₹${formatCurrency(tx.approved_amount)}` : "Unknown",
                    icon: "❓"
                };
        }
    };

    // Extract person name from owed_by_snapshot changes
    const extractPerson = (tx) => {
        if (!tx.owed_by_snapshot) return "";
        const names = Object.keys(tx.owed_by_snapshot);
        if (names.length === 1) return names[0];
        // If multiple, try to find the one that changed
        return names.length > 0 ? names[0] : "";
    };

    // Render Transaction Audit Feed
    const renderLogs = () => {
        if (transactions.length === 0) {
            logsFeed.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="12" cy="12" r="10"></circle>
                        <path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01" />
                    </svg>
                    <p>No evaluations yet. Test the CFO above!</p>
                </div>
            `;
            return;
        }

        logsFeed.innerHTML = "";
        
        // Precompute balance deltas for each transaction
        // Transactions are newest-first. Index 0 is newest.
        // To compute delta: compare this tx's remaining_balance to the PREVIOUS tx's remaining_balance
        // The "previous" tx (chronologically earlier) is at index + 1 in the array.
        // The very oldest tx (last in array) compares against the initial balance of 5000.
        const deltas = transactions.map((tx, idx) => {
            let prevBalance;
            if (idx < transactions.length - 1) {
                prevBalance = transactions[idx + 1].remaining_balance;
            } else {
                // Oldest transaction — compare against initial balance
                prevBalance = DEFAULT_BALANCE;
            }
            return tx.remaining_balance - prevBalance;
        });

        transactions.forEach((tx, idx) => {
            const action = tx.action_taken || "UNKNOWN";
            const delta = deltas[idx];
            const display = getActionDisplay(action, tx, delta);

            const logCard = document.createElement("div");
            logCard.className = `log-card ${display.cardClass}`;
            
            // Build person tag HTML if applicable
            let personHtml = "";
            if (display.personLabel) {
                personHtml = `<span class="person-tag">${escapeHtml(display.personLabel)}</span>`;
            }

            logCard.innerHTML = `
                <div class="log-header">
                    <div class="log-meta">
                        <span class="log-time">${formatTime(tx.timestamp)}</span>
                        <p class="log-pitch">"${escapeHtml(tx.expense_text)}"</p>
                    </div>
                    <div class="log-chips">
                        ${personHtml}
                        <span class="decision-chip ${display.chipClass}">
                            ${display.chipText}
                        </span>
                    </div>
                </div>
                <div class="cfo-roast-wrapper">
                    <div class="cfo-avatar">CFO</div>
                    <div class="cfo-message">${escapeHtml(tx.message)}</div>
                </div>
                <div class="log-balance-tag">Balance: ₹${formatCurrency(tx.remaining_balance)}</div>
            `;
            logsFeed.appendChild(logCard);
        });
    };

    const DEFAULT_BALANCE = 5000;

    // Draw luxury custom SVG runway chart
    const drawRunwayChart = () => {
        const svgWidth = 600;
        const svgHeight = 80;
        const paddingLeft = 10;
        const paddingRight = 10;
        const paddingTop = 15;
        const paddingBottom = 15;

        // Reset elements inside SVG
        runwayChartEl.innerHTML = `
            <defs>
                <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="var(--color-indigo)" stop-opacity="0.25"></stop>
                    <stop offset="100%" stop-color="var(--color-indigo)" stop-opacity="0.0"></stop>
                </linearGradient>
            </defs>
        `;

        const chartUsableWidth = svgWidth - paddingLeft - paddingRight;
        const chartUsableHeight = svgHeight - paddingTop - paddingBottom;

        // Build balance history from transactions (oldest to newest)
        // Transactions are stored newest-first, so reverse to get chronological order
        let balancePoints = [DEFAULT_BALANCE];
        const chronTx = [...transactions].reverse();
        chronTx.forEach(tx => {
            balancePoints.push(tx.remaining_balance);
        });

        // Make sure current live balance is the last point
        if (balancePoints[balancePoints.length - 1] !== currentBalance) {
            balancePoints.push(currentBalance);
        }

        // Dynamically compute max balance for the Y-axis
        // (handles cases where balance goes above 5000 via ADD_FUNDS or SET_EXACT_BALANCE)
        const maxBalance = Math.max(DEFAULT_BALANCE, ...balancePoints);

        // Determine point spacing based on actual number of data points
        const numPoints = balancePoints.length;
        const maxSlots = Math.max(numPoints, 3); // At least 3 slots for visual spacing
        const stepX = chartUsableWidth / (maxSlots - 1);

        // Map values to SVG coordinates
        let points = balancePoints.map((val, idx) => {
            const x = paddingLeft + idx * stepX;
            const ratio = Math.max(val / maxBalance, 0);
            const y = paddingTop + (1 - ratio) * chartUsableHeight;
            return { x, y };
        });

        // Trim to fit if too many points (downsample if more than 20)
        if (points.length > 20) {
            const step = Math.ceil(points.length / 20);
            const sampled = [points[0]];
            for (let i = step; i < points.length - 1; i += step) {
                sampled.push(points[i]);
            }
            sampled.push(points[points.length - 1]);
            // Recalculate X positions for evenly spaced
            const newStepX = chartUsableWidth / (sampled.length - 1);
            sampled.forEach((p, idx) => { p.x = paddingLeft + idx * newStepX; });
            points = sampled;
        }

        // 1. Draw Dotted Projection Line to end
        if (points.length > 0) {
            const lastPoint = points[points.length - 1];
            const endX = paddingLeft + chartUsableWidth;
            if (lastPoint.x < endX - 5) {
                const projLine = document.createElementNS("http://www.w3.org/2000/svg", "path");
                projLine.setAttribute("class", "chart-projection");
                projLine.setAttribute("d", `M ${lastPoint.x} ${lastPoint.y} L ${endX} ${lastPoint.y}`);
                runwayChartEl.appendChild(projLine);
            }
        }

        // 2. Draw Actual Balance Line
        if (points.length > 0) {
            let dLine = `M ${points[0].x} ${points[0].y}`;
            let dArea = `M ${points[0].x} ${svgHeight - paddingBottom} L ${points[0].x} ${points[0].y}`;
            
            for (let i = 1; i < points.length; i++) {
                dLine += ` L ${points[i].x} ${points[i].y}`;
                dArea += ` L ${points[i].x} ${points[i].y}`;
            }
            
            dArea += ` L ${points[points.length - 1].x} ${svgHeight - paddingBottom} Z`;

            // Draw Area fill
            const areaPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
            areaPath.setAttribute("fill", "url(#chartGrad)");
            areaPath.setAttribute("d", dArea);
            runwayChartEl.appendChild(areaPath);

            // Draw Line
            const linePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
            linePath.setAttribute("class", "chart-line");
            linePath.setAttribute("d", dLine);
            runwayChartEl.appendChild(linePath);

            // Draw glowing dot on latest balance point
            const latestPoint = points[points.length - 1];
            const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
            dot.setAttribute("class", "chart-dot");
            dot.setAttribute("cx", latestPoint.x);
            dot.setAttribute("cy", latestPoint.y);
            dot.setAttribute("r", 6);
            runwayChartEl.appendChild(dot);
        }
    };

    // Start loading transition animation
    const startLoaderCycle = () => {
        thinkingLoader.classList.remove("hidden");
        let roastIdx = 0;
        
        loaderTitle.textContent = loadingRoasts[0].title;
        loaderSubtitle.textContent = loadingRoasts[0].subtitle;

        loaderInterval = setInterval(() => {
            roastIdx = (roastIdx + 1) % loadingRoasts.length;
            loaderTitle.textContent = loadingRoasts[roastIdx].title;
            loaderSubtitle.textContent = loadingRoasts[roastIdx].subtitle;
        }, 1200);
    };

    const stopLoaderCycle = () => {
        clearInterval(loaderInterval);
        thinkingLoader.classList.add("hidden");
    };

    // Submitting expense sandbox pitching
    expenseForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const text = expenseInput.value.trim();
        if (!text) return;

        startLoaderCycle();

        try {
            const res = await fetch("/cfo-check", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ expense_text: text })
            });

            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail || "Server Error evaluating purchase");
            }

            expenseInput.value = "";
            await fetchState(true); // Fetch and animate balance change

        } catch (err) {
            alert(`Error pitching expense: ${err.message}`);
        } finally {
            stopLoaderCycle();
        }
    });

    // Resetting Runway Controls
    resetBtn.addEventListener("click", async () => {
        if (!confirm("Are you sure you want to reset your remaining runway balance back to ₹5,000 and wipe all evaluation logs?")) return;
        
        try {
            const res = await fetch("/cfo-reset", { method: "POST" });
            if (!res.ok) throw new Error("Failed to reset runway");
            
            await fetchState(true);
            alert("Remaining runway successfully reset back to ₹5,000.");
        } catch (err) {
            alert(`Reset error: ${err.message}`);
        }
    });

    // Shortcuts click handlers
    document.querySelectorAll(".shortcut-tag").forEach(tag => {
        tag.addEventListener("click", () => {
            expenseInput.value = tag.getAttribute("data-value");
            expenseInput.focus();
        });
    });

    // Initial Load Sequence
    fetchState(false);
});
