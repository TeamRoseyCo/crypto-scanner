export async function initGoogleSheets(sheetsUrl) {
    // Google Sheets API configuration
    const API_KEY = 'YOUR_GOOGLE_API_KEY';
    const SHEETS_ID = extractSheetsId(sheetsUrl);

    async function loadSheetsData() {
        try {
            const response = await fetch(
                `https://sheets.googleapis.com/v4/spreadsheets/${SHEETS_ID}/values/Sheet1?key=${API_KEY}`
            );
            return await response.json();
        } catch (error) {
            console.error('Error loading sheets data:', error);
            throw error;
        }
    }

    return await loadSheetsData();
}

function extractSheetsId(url) {
    const regex = /spreadsheets\/d\/([a-zA-Z0-9-_]+)/;
    const match = url.match(regex);
    return match ? match[1] : null;
}
