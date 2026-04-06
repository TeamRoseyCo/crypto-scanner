import { initGoogleSheets } from './sheets.js';

class TradingBot {
    constructor() {
        this.sheetsUrl = '';
        this.init();
    }

    init() {
        const connectButton = document.getElementById('connect-sheets');
        const sheetsInput = document.getElementById('sheets-url');
        const statusDisplay = document.getElementById('status-display');

        connectButton.addEventListener('click', async () => {
            this.sheetsUrl = sheetsInput.value;
            try {
                await this.connectToSheets();
                statusDisplay.innerHTML = 'Connected to Google Sheets';
                statusDisplay.style.color = 'green';
            } catch (error) {
                statusDisplay.innerHTML = 'Failed to connect';
                statusDisplay.style.color = 'red';
            }
        });
    }

    async connectToSheets() {
        if (!this.sheetsUrl) throw new Error('No sheets URL provided');
        return await initGoogleSheets(this.sheetsUrl);
    }
}

// Initialize the trading bot
new TradingBot();
