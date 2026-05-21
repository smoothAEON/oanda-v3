import { useEffect, useState } from 'react';
import { callMcpTool } from '../api';
import { DollarSign, Percent, TrendingUp, Layers, ArrowDownLeft, ArrowUpRight } from 'lucide-react';

export default function AccountDashboard() {
  const [summary, setSummary] = useState(null);
  const [positions, setPositions] = useState([]);
  const [orders, setOrders] = useState([]);
  const [transfers, setTransfers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const [accSum, posData, ordData, transferData] = await Promise.all([
          callMcpTool("get_account_summary"),
          callMcpTool("list_open_positions"),
          callMcpTool("list_open_orders"),
          callMcpTool("list_transfers", { limit: 10 }).catch(err => {
            console.error("Transfers fetch error:", err);
            return { transfers: [] };
          })
        ]);
        
        setSummary(accSum);
        setPositions(posData?.positions || []);
        setOrders(ordData?.orders || []);
        const rawTransfers = transferData?.transfers || [];
        console.log('[Transfers] raw data:', rawTransfers);
        setTransfers(rawTransfers);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  if (loading) return <div style={{ padding: '2rem' }} className="animate-fade-in">Loading Account Data...</div>;
  if (error) return <div style={{ padding: '2rem', color: 'var(--accent-red)' }}>Error: {error}</div>;

  return (
    <div style={{ padding: '2rem' }} className="animate-fade-in">
      <header className="glass-header" style={{ margin: '-2rem -2rem 2rem -2rem', padding: '2rem' }}>
        <h1>Account Overview</h1>
        <p>Real-time OANDA account status and active positions</p>
      </header>

      {summary && (
        <div className="grid-4" style={{ marginBottom: '2rem' }}>
          <div className="card glass-panel">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="stat-label">Balance</span>
              <DollarSign size={18} color="var(--accent-blue)" />
            </div>
            <div className="stat-value">{summary.balance}</div>
            <div className="stat-label">{summary.currency}</div>
          </div>
          
          <div className="card glass-panel">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="stat-label">NAV</span>
              <Layers size={18} color="var(--accent-green)" />
            </div>
            <div className="stat-value">{summary.nav}</div>
            <div className="stat-label">Unrealized: <span style={{ color: summary.unrealized_pl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>{summary.unrealized_pl}</span></div>
          </div>

          <div className="card glass-panel">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="stat-label">Margin Used</span>
              <Percent size={18} color="var(--accent-orange)" />
            </div>
            <div className="stat-value">{summary.margin_used}</div>
            <div className="stat-label">Available: {summary.margin_available}</div>
          </div>

          <div className="card glass-panel">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="stat-label">Open Trades</span>
              <TrendingUp size={18} color="var(--accent-blue)" />
            </div>
            <div className="stat-value">{summary.open_trade_count}</div>
            <div className="stat-label">Pending Orders: {summary.pending_order_count}</div>
          </div>
        </div>
      )}

      <div className="grid-2">
        <div className="card glass-panel">
          <h2>Open Positions</h2>
          {positions.length === 0 ? (
            <p>No open positions.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Units</th>
                  <th>Unrealized PnL</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 600 }}>{p.instrument}</td>
                    <td>{p.long?.units || p.short?.units}</td>
                    <td style={{ color: p.unrealized_pl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                      {p.unrealized_pl}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card glass-panel">
          <h2>Pending Orders</h2>
          {orders.length === 0 ? (
            <p>No pending orders.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Type</th>
                  <th>Units</th>
                  <th>Price</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 600 }}>{o.instrument}</td>
                    <td><span className="badge badge-blue">{o.type}</span></td>
                    <td>{o.units}</td>
                    <td>{o.price}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Funds Transfers & Withdrawals */}
      <div className="card glass-panel" style={{ marginTop: '2rem' }}>
        <h2 style={{ fontSize: '1.25rem', marginBottom: '1.25rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <DollarSign size={20} color="var(--accent-blue)" /> Funds Transfers & Withdrawals
        </h2>
        {transfers.length === 0 ? (
          <p style={{ color: 'var(--text-secondary)' }}>No recent transfers or withdrawals recorded.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date & Time</th>
                  <th>Transaction ID</th>
                  <th>Type</th>
                  <th>Funding Reason</th>
                  <th>Comment</th>
                  <th>Amount</th>
                  <th>Resulting Balance</th>
                </tr>
              </thead>
              <tbody>
                {transfers.map((tr, trIdx) => {
                  // Safely resolve amount — field may be 'amount' or 'units'
                  const rawAmount = tr.amount ?? tr.units ?? 0;
                  const amountVal = isNaN(parseFloat(rawAmount)) ? 0 : parseFloat(rawAmount);
                  const isDeposit = amountVal >= 0;
                  // Safely resolve balance — field may be 'accountBalance' or 'balance'
                  const rawBalance = tr.accountBalance ?? tr.balance ?? null;
                  const balanceVal = rawBalance != null && !isNaN(parseFloat(rawBalance)) ? parseFloat(rawBalance) : null;
                  return (
                    <tr key={tr.id ?? trIdx}>
                      <td style={{ color: 'var(--text-secondary)' }}>
                        {tr.time ? new Date(tr.time).toLocaleString() : '—'}
                      </td>
                      <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>#{tr.id ?? '—'}</td>
                      <td>
                        <span 
                          className={isDeposit ? 'badge badge-green' : 'badge badge-red'}
                          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}
                        >
                          {isDeposit ? <ArrowDownLeft size={12} /> : <ArrowUpRight size={12} />}
                          {isDeposit ? 'Deposit' : 'Withdrawal'}
                        </span>
                      </td>
                      <td style={{ color: 'var(--text-secondary)' }}>
                        {(tr.fundingReason ?? tr.funding_reason ?? '').replace(/_/g, ' ') || 'N/A'}
                      </td>
                      <td style={{ color: 'var(--text-secondary)' }}>
                        {tr.comment || '—'}
                      </td>
                      <td style={{ 
                        fontWeight: 700, 
                        color: isDeposit ? 'var(--accent-green)' : 'var(--accent-red)' 
                      }}>
                        {isDeposit ? '+' : ''}{amountVal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td style={{ fontWeight: 600, fontFamily: 'monospace' }}>
                        {balanceVal != null
                          ? balanceVal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                          : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
