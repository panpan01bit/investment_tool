import { useEffect, useRef } from 'react';
import { dispose, init } from 'klinecharts';

export interface Candle {
  timestamp: number;   // 毫秒
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const DARK_GRID = '#1f2937';
const DARK_TEXT = '#9ca3af';

/**
 * 专业K线图（klinecharts）：主图蜡烛+MA/BOLL，副图 VOL/MACD/RSI。
 * 载入 /api/candles 返回的 klines。
 */
export default function CandleChart({ klines, height = 460 }: { klines: Candle[]; height?: number }) {
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!boxRef.current) return;
    const chart = init(boxRef.current, {
      styles: {
        grid: { horizontal: { color: DARK_GRID }, vertical: { show: false } },
        candle: {
          bar: {
            upColor: '#ef4444', downColor: '#22c55e',       // A股习惯：涨红跌绿
            upBorderColor: '#ef4444', downBorderColor: '#22c55e',
            upWickColor: '#ef4444', downWickColor: '#22c55e',
          },
          priceMark: { high: { color: DARK_TEXT }, low: { color: DARK_TEXT } },
        },
        indicator: {
          bars: [{ upColor: '#ef4444', downColor: '#22c55e' }],
        },
        xAxis: { axisLine: { color: DARK_GRID }, tickText: { color: DARK_TEXT } },
        yAxis: { axisLine: { color: DARK_GRID }, tickText: { color: DARK_TEXT } },
        crosshair: {
          horizontal: { line: { color: '#4b5563' }, text: { color: DARK_TEXT } },
          vertical: { line: { color: '#4b5563' }, text: { color: DARK_TEXT } },
        },
      },
    });
    if (!chart) return;

    chart.applyNewData(klines);
    chart.createIndicator('MA', false);
    chart.createIndicator('VOL', true);
    chart.createIndicator('MACD', true);
    chart.createIndicator('RSI', true);

    const box = boxRef.current;
    return () => {
      if (box) dispose(box);
    };
  }, [klines]);

  if (!klines.length) {
    return <div className="muted" style={{ padding: 16 }}>暂无K线数据</div>;
  }
  return <div ref={boxRef} style={{ width: '100%', height }} />;
}
