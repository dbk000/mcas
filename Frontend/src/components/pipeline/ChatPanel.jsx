import { AlertTriangle, Server, ShieldAlert, X } from 'lucide-react'

function DetailRow({ label, value }) {
  if (!value) return null

  return (
    <div className="rounded-xl border border-terminal-700 bg-terminal-900/70 p-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-terminal-500">{label}</div>
      <div className="mt-2 text-xs text-terminal-200">{value}</div>
    </div>
  )
}

export default function ChatPanel({ selectedNode, onClose }) {
  return (
    <div className="fixed right-0 top-0 z-50 flex h-full w-[400px] flex-col border-l border-terminal-600 bg-terminal-800 shadow-xl">
      <div className="flex items-center justify-between border-b border-terminal-600 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-terminal-100">AI Chat</h2>
          <p className="text-[10px] text-terminal-500">Disabled in the browser-only build</p>
        </div>
        <button onClick={onClose} className="p-1 text-terminal-400 transition-colors hover:text-terminal-100">
          <X className="size-4" />
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <section className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4">
          <div className="inline-flex items-center gap-2 rounded-full border border-amber-500/30 bg-terminal-900/70 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-amber-200">
            <ShieldAlert className="size-3.5" />
            Browser Trust Boundary
          </div>
          <h3 className="mt-4 text-lg font-semibold text-terminal-100">Chat needs a server-backed proxy before it can return.</h3>
          <p className="mt-3 text-sm leading-6 text-terminal-300">
            This panel no longer accepts browser-stored model keys or sends direct model-provider requests from the client.
            Re-enable chat only after a backend endpoint exists to hold vendor credentials and execute tool actions on the server side.
          </p>
        </section>

        <section className="grid gap-3">
          <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-terminal-400">
              <AlertTriangle className="size-3.5 text-amber-300" />
              Removed For Safety
            </div>
            <ul className="mt-3 space-y-2 text-xs leading-5 text-terminal-300">
              <li>Browser persistence of third-party model credentials.</li>
              <li>Direct browser-to-vendor inference requests.</li>
              <li>Tool-capable chat execution from an untrusted client surface.</li>
            </ul>
          </div>

          <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-terminal-400">
              <Server className="size-3.5 text-emerald-300" />
              Safe Restore Path
            </div>
            <ul className="mt-3 space-y-2 text-xs leading-5 text-terminal-300">
              <li>Add a server route that owns vendor secrets and request policy.</li>
              <li>Move tool authorization and audit logging behind that route.</li>
              <li>Keep the browser limited to session state and rendered results.</li>
            </ul>
          </div>
        </section>

        {selectedNode && (
          <section className="space-y-3 rounded-2xl border border-terminal-700 bg-terminal-800/70 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-terminal-500">Selected Context</div>
            <DetailRow label="Node" value={selectedNode.name} />
            <DetailRow label="Subsystem" value={selectedNode.subsystem} />
            <DetailRow label="Type" value={selectedNode.type} />
            <DetailRow label="Description" value={selectedNode.description} />
          </section>
        )}
      </div>
    </div>
  )
}
