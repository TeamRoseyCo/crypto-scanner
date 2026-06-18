import { config } from 'dotenv';
import path from 'path';
import { testBybitConnection } from '../scripts/validation';

// Load environment variables from the correct path
config({ path: path.resolve(__dirname, '../app/.env.local') });

async function main() {
  const apiKey = process.env.BYBIT_API_KEY;
  const apiSecret = process.env.BYBIT_API_SECRET;

  if (!apiKey || !apiSecret) {
    console.error('API keys not found in environment variables!');
    process.exit(1);
  }

  const isValid = await testBybitConnection(apiKey, apiSecret);

  if (isValid) {
    console.log('✅ API connection validated successfully!');
  } else {
    console.log('❌ API connection failed!');
  }
}

main().catch(console.error);