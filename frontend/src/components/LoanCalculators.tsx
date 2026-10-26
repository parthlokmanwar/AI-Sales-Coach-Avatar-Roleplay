import { useState } from 'react'
import {
  Calculator, PieChart, TrendingUp, Briefcase,
  Building2, Activity, BarChart2, ChevronDown,
} from 'lucide-react'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number): string {
  if (!isFinite(n) || isNaN(n)) return '—'
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

function fmtDec(n: number): string {
  if (!isFinite(n) || isNaN(n)) return '—'
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
}

// ── Shared form primitives ────────────────────────────────────────────────────

interface FieldProps {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  min?: string
  max?: string
}

function Field({ label, value, onChange, placeholder, min, max }: FieldProps) {
  return (
    <div className="lc-field">
      <label className="lc-label">{label}</label>
      <input
        className="lc-input"
        type="number"
        min={min ?? '0'}
        max={max}
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
      />
    </div>
  )
}

interface ResultProps { rows: { label: string; value: React.ReactNode; highlight?: boolean }[] }

function Result({ rows }: ResultProps) {
  return (
    <div className="lc-result">
      {rows.map((r, i) => (
        <div key={i} className={`lc-result-row${r.highlight ? ' highlight' : ''}`}>
          <span className="lc-result-label">{r.label}</span>
          <span className="lc-result-value">{r.value}</span>
        </div>
      ))}
    </div>
  )
}

function CalcBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button className="btn btn-primary lc-btn" onClick={onClick}>{label}</button>
  )
}

// ── 1. EMI ────────────────────────────────────────────────────────────────────

function EmiBody() {
  const [P, setP] = useState('')
  const [rate, setRate] = useState('')
  const [n, setN] = useState('')
  const [rows, setRows] = useState<ResultProps['rows'] | null>(null)

  const calc = () => {
    const p = +P, R = +rate / 1200, months = +n
    if (!p || !R || !months) return
    const pow = Math.pow(1 + R, months)
    const emi = (p * R * pow) / (pow - 1)
    const total = emi * months
    setRows([
      { label: 'Monthly EMI',    value: <strong>{fmtDec(emi)}</strong>, highlight: true },
      { label: 'Total Interest', value: fmtDec(total - p) },
      { label: 'Total Repayment',value: fmtDec(total) },
    ])
  }

  return (
    <>
      <Field label="Loan Amount (₹)" value={P} onChange={setP} placeholder="e.g. 500000" />
      <Field label="Annual Rate (%)" value={rate} onChange={setRate} placeholder="e.g. 12" />
      <Field label="Tenure (months)" value={n} onChange={setN} placeholder="e.g. 36" min="1" />
      <CalcBtn label="Calculate EMI" onClick={calc} />
      {rows && <Result rows={rows} />}
    </>
  )
}

// ── 2. FOIR ───────────────────────────────────────────────────────────────────

function FoirBody() {
  const [income, setIncome] = useState('')
  const [emi, setEmi] = useState('')
  const [rows, setRows] = useState<ResultProps['rows'] | null>(null)

  const calc = () => {
    const inc = +income
    if (!inc) return
    const foir = (+emi / inc) * 100
    const ok = foir <= 50
    setRows([
      {
        label: 'FOIR',
        value: <strong style={{ color: ok ? 'var(--color-success)' : 'var(--color-red)' }}>{foir.toFixed(2)}%</strong>,
        highlight: true,
      },
      {
        label: 'Status',
        value: <span style={{ color: ok ? 'var(--color-success)' : 'var(--color-red)', fontWeight: 600 }}>
          {ok ? '✓ Within 50% limit' : '⚠ Exceeds 50% limit'}
        </span>,
      },
    ])
  }

  return (
    <>
      <Field label="Monthly Income (₹)" value={income} onChange={setIncome} placeholder="e.g. 80000" />
      <Field label="Existing EMI (₹)"   value={emi}    onChange={setEmi}    placeholder="e.g. 20000" />
      <CalcBtn label="Calculate FOIR" onClick={calc} />
      {rows && <Result rows={rows} />}
    </>
  )
}

// ── 3. Loan Eligibility ───────────────────────────────────────────────────────

function EligibilityBody() {
  const [income, setIncome]           = useState('')
  const [existingEmi, setExistingEmi] = useState('')
  const [foirPct, setFoirPct]         = useState('50')
  const [rate, setRate]               = useState('12')
  const [tenure, setTenure]           = useState('60')
  const [rows, setRows]               = useState<ResultProps['rows'] | null>(null)

  const calc = () => {
    const inc = +income, e = +existingEmi, f = +foirPct
    const R = +rate / 1200, nt = +tenure
    if (!inc || !f) return
    const eligibleEmi = Math.max(0, inc * f / 100 - e)
    const maxLoan = R > 0 && nt > 0 ? eligibleEmi * (1 - Math.pow(1 + R, -nt)) / R : 0
    setRows([
      { label: 'Max Eligible EMI',  value: <strong>{fmtDec(eligibleEmi)}</strong> },
      { label: 'Max Loan Amount',   value: <strong>{fmt(maxLoan)}</strong>, highlight: true },
    ])
  }

  return (
    <>
      <Field label="Monthly Income (₹)" value={income}      onChange={setIncome}      placeholder="e.g. 80000" />
      <Field label="Existing EMI (₹)"   value={existingEmi} onChange={setExistingEmi} placeholder="e.g. 0" />
      <div className="lc-field-row">
        <Field label="FOIR Limit (%)"  value={foirPct} onChange={setFoirPct} placeholder="50"  max="100" />
        <Field label="Rate (% p.a.)"   value={rate}    onChange={setRate}    placeholder="12" />
        <Field label="Tenure (months)" value={tenure}  onChange={setTenure}  placeholder="60"  min="1" />
      </div>
      <CalcBtn label="Check Eligibility" onClick={calc} />
      {rows && <Result rows={rows} />}
    </>
  )
}

// ── 4. Working Capital ────────────────────────────────────────────────────────

function WorkingCapitalBody() {
  const [inventory,   setInventory]   = useState('')
  const [receivables, setReceivables] = useState('')
  const [payables,    setPayables]    = useState('')
  const [rows, setRows] = useState<ResultProps['rows'] | null>(null)

  const calc = () => {
    const wc = +inventory + +receivables - +payables
    setRows([
      { label: 'Working Capital Req.', value: <strong>{fmt(wc)}</strong>, highlight: true },
      {
        label: 'Funding Need',
        value: <span style={{ color: wc > 0 ? 'var(--color-warning)' : 'var(--color-success)', fontWeight: 600 }}>
          {wc > 0 ? `Needs ${fmt(wc)} funding` : 'Self-funded ✓'}
        </span>,
      },
    ])
  }

  return (
    <>
      <Field label="Inventory (₹)"   value={inventory}   onChange={setInventory}   placeholder="e.g. 200000" />
      <Field label="Receivables (₹)" value={receivables} onChange={setReceivables} placeholder="e.g. 100000" />
      <Field label="Payables (₹)"    value={payables}    onChange={setPayables}    placeholder="e.g. 50000" />
      <CalcBtn label="Calculate" onClick={calc} />
      {rows && <Result rows={rows} />}
    </>
  )
}

// ── 5. GST Loan Eligibility ───────────────────────────────────────────────────

function GstBody() {
  const [turnover, setTurnover] = useState('')
  const [rows, setRows] = useState<ResultProps['rows'] | null>(null)

  const calc = () => {
    const t = +turnover
    if (!t) return
    setRows([
      { label: 'Min loan (15%)', value: fmt(t * 0.15) },
      { label: 'Max loan (30%)', value: <strong>{fmt(t * 0.30)}</strong>, highlight: true },
    ])
  }

  return (
    <>
      <Field label="Annual GST Turnover (₹)" value={turnover} onChange={setTurnover} placeholder="e.g. 5000000" />
      <CalcBtn label="Calculate Range" onClick={calc} />
      {rows && <Result rows={rows} />}
    </>
  )
}

// ── 6. Cash Flow ──────────────────────────────────────────────────────────────

function CashFlowBody() {
  const [revenue,  setRevenue]  = useState('')
  const [expenses, setExpenses] = useState('')
  const [debt,     setDebt]     = useState('')
  const [rows, setRows] = useState<ResultProps['rows'] | null>(null)

  const calc = () => {
    const cf = +revenue - +expenses - +debt
    const ok = cf >= 0
    setRows([
      {
        label: 'Net Cash Flow',
        value: <strong style={{ color: ok ? 'var(--color-success)' : 'var(--color-red)' }}>{fmtDec(cf)}</strong>,
        highlight: true,
      },
      {
        label: 'Repayment Capacity',
        value: <span style={{ color: ok ? 'var(--color-success)' : 'var(--color-red)', fontWeight: 600 }}>
          {ok ? '✓ Can service new loan' : '⚠ Negative cash flow'}
        </span>,
      },
    ])
  }

  return (
    <>
      <Field label="Revenue (₹)"       value={revenue}  onChange={setRevenue}  placeholder="e.g. 1000000" />
      <Field label="Expenses (₹)"      value={expenses} onChange={setExpenses} placeholder="e.g. 600000" />
      <Field label="Existing Debt (₹)" value={debt}     onChange={setDebt}     placeholder="e.g. 100000" />
      <CalcBtn label="Calculate" onClick={calc} />
      {rows && <Result rows={rows} />}
    </>
  )
}

// ── 7. ROI ────────────────────────────────────────────────────────────────────

function RoiBody() {
  const [investment, setInvestment] = useState('')
  const [profit,     setProfit]     = useState('')
  const [rows, setRows] = useState<ResultProps['rows'] | null>(null)

  const calc = () => {
    const inv = +investment
    if (!inv) return
    const roi = (+profit / inv) * 100
    const ok = roi >= 0
    setRows([
      {
        label: 'ROI',
        value: <strong style={{ color: ok ? 'var(--color-success)' : 'var(--color-red)' }}>{roi.toFixed(2)}%</strong>,
        highlight: true,
      },
      { label: 'Payback Period', value: roi > 0 ? `~${(100 / roi).toFixed(1)} years` : '—' },
    ])
  }

  return (
    <>
      <Field label="Investment (₹)" value={investment} onChange={setInvestment} placeholder="e.g. 1000000" />
      <Field label="Net Profit (₹)" value={profit}     onChange={setProfit}     placeholder="e.g. 200000" />
      <CalcBtn label="Calculate ROI" onClick={calc} />
      {rows && <Result rows={rows} />}
    </>
  )
}

// ── Accordion config ──────────────────────────────────────────────────────────

const CALC_ITEMS = [
  { id: 'emi',      icon: Calculator, title: 'EMI Calculator',        accent: '#E31837', tint: 'rgba(227,24,55,0.06)',   Body: EmiBody },
  { id: 'foir',     icon: PieChart,   title: 'FOIR Calculator',       accent: '#0A1628', tint: 'rgba(10,22,40,0.05)',    Body: FoirBody },
  { id: 'elig',     icon: TrendingUp, title: 'Loan Eligibility',      accent: '#10B981', tint: 'rgba(16,185,129,0.06)',  Body: EligibilityBody },
  { id: 'wc',       icon: Briefcase,  title: 'Working Capital',       accent: '#F59E0B', tint: 'rgba(245,158,11,0.06)', Body: WorkingCapitalBody },
  { id: 'gst',      icon: Building2,  title: 'GST Loan Eligibility',  accent: '#7C3AED', tint: 'rgba(124,58,237,0.06)', Body: GstBody },
  { id: 'cashflow', icon: Activity,   title: 'Cash Flow Calculator',  accent: '#0EA5E9', tint: 'rgba(14,165,233,0.06)', Body: CashFlowBody },
  { id: 'roi',      icon: BarChart2,  title: 'ROI Calculator',        accent: '#E31837', tint: 'rgba(227,24,55,0.06)',  Body: RoiBody },
] as const

// ── Main export ───────────────────────────────────────────────────────────────

export default function LoanCalculators() {
  const [openId, setOpenId] = useState<string>('emi')

  return (
    <div className="lc-accordion">
      {CALC_ITEMS.map(({ id, icon: Icon, title, accent, tint, Body }) => {
        const isOpen = openId === id
        return (
          <div
            key={id}
            className={`lc-acc-item${isOpen ? ' open' : ''}`}
            style={{ '--lc-accent': accent, '--lc-tint': tint } as React.CSSProperties}
          >
            <button
              className="lc-acc-header"
              onClick={() => setOpenId(isOpen ? '' : id)}
              aria-expanded={isOpen}
            >
              <div className="lc-acc-icon">
                <Icon size={15} color="#fff" />
              </div>
              <span className="lc-acc-title">{title}</span>
              <ChevronDown size={16} className={`lc-acc-chevron${isOpen ? ' open' : ''}`} />
            </button>
            {isOpen && (
              <div className="lc-acc-body">
                <Body />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
