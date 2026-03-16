import { useState } from 'react'

export default function SymbolMultiSelect({ selected, onChange, markets }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)

  const allSymbols = (markets?.markets || markets || [])
    .map((market) => (typeof market === 'string' ? market : market.symbol))
    .filter(Boolean)
    .sort()

  const filteredSymbols = query
    ? allSymbols.filter((symbol) => symbol.toLowerCase().includes(query.toLowerCase()))
    : allSymbols

  const toggleSymbol = (symbol) => {
    if (selected.includes(symbol)) {
      onChange(selected.filter((item) => item !== symbol))
      return
    }
    onChange([...selected, symbol])
  }

  const removeSymbol = (symbol) => onChange(selected.filter((item) => item !== symbol))

  return (
    <div className="relative">
      <div
        className="flex min-h-[42px] cursor-text flex-wrap gap-1.5 rounded border border-terminal-500 bg-terminal-800 p-2 focus-within:border-amber-500 focus-within:ring-1 focus-within:ring-amber-500/20"
        onClick={() => setOpen(true)}
      >
        {selected.map((symbol) => (
          <span
            key={symbol}
            className="inline-flex items-center gap-1 rounded bg-amber-500/20 px-2 py-0.5 font-mono text-xs text-amber-400"
          >
            {symbol}
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                removeSymbol(symbol)
              }}
              className="hover:text-amber-200"
            >
              &times;
            </button>
          </span>
        ))}
        <input
          type="text"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 200)}
          placeholder={selected.length === 0 ? 'Type to search symbols...' : ''}
          className="min-w-[120px] flex-1 bg-transparent text-sm text-terminal-100 outline-none placeholder:text-terminal-400"
        />
      </div>

      {open && filteredSymbols.length > 0 && (
        <div className="absolute z-50 mt-1 max-h-48 w-full overflow-y-auto rounded-lg border border-terminal-600 bg-terminal-800 shadow-xl">
          {filteredSymbols.slice(0, 50).map((symbol) => (
            <button
              key={symbol}
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                toggleSymbol(symbol)
                setQuery('')
              }}
              className={`w-full px-3 py-1.5 text-left font-mono text-sm hover:bg-terminal-700 ${
                selected.includes(symbol) ? 'bg-amber-500/10 text-amber-400' : 'text-terminal-200'
              }`}
            >
              {symbol}
              {selected.includes(symbol) && <span className="float-right text-amber-500">&#10003;</span>}
            </button>
          ))}
          {filteredSymbols.length > 50 && (
            <div className="px-3 py-1.5 text-xs text-terminal-500">
              {filteredSymbols.length - 50} more — type to narrow
            </div>
          )}
        </div>
      )}

      {open && allSymbols.length === 0 && (
        <div className="absolute z-50 mt-1 w-full rounded-lg border border-terminal-600 bg-terminal-800 p-3 text-sm text-terminal-400 shadow-xl">
          No markets loaded. Check API connection.
        </div>
      )}
    </div>
  )
}
