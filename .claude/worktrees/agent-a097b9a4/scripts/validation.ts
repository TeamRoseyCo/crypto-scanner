export async function testBybitConnection(apiKey: string, apiSecret: string) {
  try {
    const bybit = (await import('bybit-api')) as any;
    const RESTClientClass = bybit.RESTClient || bybit.REST || bybit.default?.RESTClient || bybit.default;
    const client = new RESTClientClass({
      key: apiKey,
      secret: apiSecret,
      testnet: true,
    });

    // Try to fetch account info to validate connection
    const serverTime = await client.getServerTime();
    const walletBalance = await client.getWalletBalance({ coin: 'USDT' });

    console.log('Connection successful!');
    console.log('Server time:', serverTime);
    console.log('Wallet balance:', walletBalance);

    return true;
  } catch (error) {
    console.error('Failed to connect to Bybit:', error);
    return false;
  }
}