document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements
    const balanceAmountEl = document.getElementById("balanceAmount");
    const daysLeftTextEl = document.getElementById("daysLeftText");
    const dailyBudgetEl = document.getElementById("dailyBudget");
    const totalBurnsCountEl = document.getElementById("totalBurnsCount");
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
    
    // Config Target Date
    const TARGET_DATE = new Date("2026-06-22T00:00:00");

    // Funny loader roast scripts
    const loadingRoasts = [
        { title: "Analyzing poor life choices...", subtitle: "Simulating runway collapse sequence" },
        { title: "Cross-referencing ROI metrics...", subtitle: "Evaluating standard young-adult behavior" },
        { title: "Harshly calculating runway decay...", subtitle: "Analyzing percentage of total net worth" },
        { title: "Consulting financial models...", subtitle: "Translating deep disappointment into sarcasm" },
        { title: "Preparing vocal burn...", subtitle: "Deduction validation in progress" }
    ];
    let loaderInterval = null;

    // Helper: Format large numbers with comma
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

    // Fetch and sync application state
    const fetchState = async (animate = false) => {
        try {
            const res = await fetch("/cfo-state");
            if (!res.ok) throw new Error("Failed to load CFO state");
            
            const state = await res.json();
            const prevBalance = currentBalance;
            currentBalance = state.current_balance;
            transactions = state.transactions;

            // Render stats
            const daysLeft = getDaysRemaining();
            daysLeftTextEl.textContent = `${daysLeft} Days Left`;
            
            const budgetVal = Math.max(Math.round(currentBalance / daysLeft), 0);
            dailyBudgetEl.textContent = `₹${formatCurrency(budgetVal)}/day`;
            
            // Burns Count is number of rejected transactions (approved_amount === 0)
            const totalBurns = transactions.filter(t => t.approved_amount === 0).length;
            totalBurnsCountEl.textContent = totalBurns;

            // Animate or set balance
            if (animate) {
                animateBalance(prevBalance, currentBalance);
            } else {
                balanceAmountEl.textContent = formatCurrency(currentBalance);
            }

            renderLogs();
            drawRunwayChart();

        } catch (err) {
            console.error("Error synchronizing state:", err);
        }
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
        transactions.forEach((tx) => {
            const isApproved = tx.approved_amount > 0;
            const logCard = document.createElement("div");
            logCard.className = `log-card ${isApproved ? 'approved-card' : 'rejected-card'}`;
            
            logCard.innerHTML = `
                <div class="log-header">
                    <div class="log-meta">
                        <span class="log-time">${formatTime(tx.timestamp)}</span>
                        <p class="log-pitch">"${escapeHtml(tx.expense_text)}"</p>
                    </div>
                    <span class="decision-chip ${isApproved ? 'chip-approved' : 'chip-rejected'}">
                        ${isApproved ? `+₹${tx.approved_amount} Approved` : 'Rejected (₹0)'}
                    </span>
                </div>
                <div class="cfo-roast-wrapper">
                    <div class="cfo-avatar">CFO</div>
                    <div class="cfo-message">${escapeHtml(tx.message)}</div>
                </div>
            `;
            logsFeed.appendChild(logCard);
        });
    };

    // Escape HTML helper to prevent XSS injection
    const escapeHtml = (text) => {
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, function(m) { return map[m]; });
    };

    // Draw luxury custom SVG runway decay chart
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

        // Generate data points
        // Start from ₹5,000 initial, decay downwards based on transaction chronological deductions
        let balancePoints = [5000];
        
        // Reverse transaction history to get chronological order (oldest first)
        const chronTx = [...transactions].reverse();
        let runningBalance = 5000;
        chronTx.forEach(tx => {
            if (tx.approved_amount > 0) {
                runningBalance -= tx.approved_amount;
                balancePoints.push(runningBalance);
            }
        });

        // Current balance should be the final point
        if (balancePoints[balancePoints.length - 1] !== currentBalance) {
            balancePoints.push(currentBalance);
        }

        const maxPoints = 8; // Max coordinate slots
        const stepX = chartUsableWidth / (maxPoints - 1);

        // Map values to coordinates
        let points = [];
        balancePoints.forEach((val, idx) => {
            if (idx < maxPoints) {
                const x = paddingLeft + idx * stepX;
                const ratio = Math.max(val / 5000, 0); // clamp to 0
                const y = paddingTop + (1 - ratio) * chartUsableHeight;
                points.push({ x, y });
            }
        });

        // 1. Draw Dotted Projection Line to End (June 22nd)
        const projectionPoints = [...points];
        while (projectionPoints.length < maxPoints) {
            const idx = projectionPoints.length;
            const x = paddingLeft + idx * stepX;
            // Projection is line straight or decay to zero
            const val = currentBalance;
            const ratio = Math.max(val / 5000, 0);
            const y = paddingTop + (1 - ratio) * chartUsableHeight;
            projectionPoints.push({ x, y });
        }

        // Generate SVG Path for projection line
        if (projectionPoints.length > 0) {
            let dProj = `M ${projectionPoints[0].x} ${projectionPoints[0].y}`;
            for (let i = 1; i < projectionPoints.length; i++) {
                dProj += ` L ${projectionPoints[i].x} ${projectionPoints[i].y}`;
            }
            const projLine = document.createElementNS("http://www.w3.org/2000/svg", "path");
            projLine.setAttribute("class", "chart-projection");
            projLine.setAttribute("d", dProj);
            runwayChartEl.appendChild(projLine);
        }

        // 2. Draw Actual Decay Line
        if (points.length > 0) {
            let dLine = `M ${points[0].x} ${points[0].y}`;
            let dArea = `M ${points[0].x} ${svgHeight - paddingBottom} L ${points[0].x} ${points[0].y}`;
            
            for (let i = 1; i < points.length; i++) {
                dLine += ` L ${points[i].x} ${points[i].y}`;
                dArea += ` L ${points[i].x} ${points[i].y}`;
            }
            
            dArea += ` L ${points[points.length - 1].x} ${svgHeight - paddingBottom} Z`;

            // Draw Area
            const areaPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
            areaPath.setAttribute("fill", "url(#chartGrad)");
            areaPath.setAttribute("d", dArea);
            runwayChartEl.appendChild(areaPath);

            // Draw Line
            const linePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
            linePath.setAttribute("class", "chart-line");
            linePath.setAttribute("d", dLine);
            runwayChartEl.appendChild(linePath);

            // Draw glowing dot on the latest balance point
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
            await fetchState(true); // Fetch and animate number decay!

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
