if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/static/service-worker.js").catch((err) => {
            console.warn("Service worker registration failed:", err);
        });
    });
}

const tradingForm = document.getElementById("trading-form");
if (tradingForm) {
    tradingForm.addEventListener("submit", (event) => {
        const submitter = event.submitter && event.submitter.name;
        if (submitter === "start") {
            const money = document.getElementById("money").value;
            if (!money || Number(money) <= 0) {
                event.preventDefault();
                alert("Enter an amount greater than zero to invest.");
                return;
            }
            if (!confirm("Start the trading bot with this amount? This will place real orders on your connected Alpaca account.")) {
                event.preventDefault();
            }
        }
    });
}
