export interface Strategy {
  pairs: string[];
  riskLevel: number;
  maxPosition: number;
  stopLoss: number;
  takeProfit: number;
}

export interface Position {
  pair: string;
  amount: number;
  entryPrice: number;
  currentPrice: number;
}

export interface TradeSignal {
  type: 'BUY' | 'SELL';
  pair: string;
  price: number;
  amount: number;
  confidence: number;
}
