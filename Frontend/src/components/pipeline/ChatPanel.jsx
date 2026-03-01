import { useState, useRef, useEffect } from 'react'
import { X, Send, Loader2 } from 'lucide-react'
import { useApi } from '../../context/ApiContext'

// Tool definitions for the model
const TOOL_DEFINITIONS = [
  {
    name: 'get_node_versions',
    description: 'Fetch version history for any node. Returns versions with metrics, parameters, and change reasons.',
    input_schema: {
      type: 'object',
      properties: {
        node_id: { type: 'string', description: 'UUID of the node' },
        limit: { type: 'integer', description: 'Max versions to return', default: 20 },
      },
      required: ['node_id'],
    },
  },
  {
    name: 'get_composition',
    description: 'Fetch composition details including node versions and validation gate.',
    input_schema: {
      type: 'object',
      properties: { composition_id: { type: 'string' } },
      required: ['composition_id'],
    },
  },
  {
    name: 'get_experiment',
    description: 'Fetch experiment details including hypothesis, outcome, and versions.',
    input_schema: {
      type: 'object',
      properties: { experiment_id: { type: 'string' } },
      required: ['experiment_id'],
    },
  },
  {
    name: 'search_versions',
    description: 'Search node_versions by metric thresholds. Filter by node_id, min_agg_pf, min_win_rate, max_drawdown, min_trade_count.',
    input_schema: {
      type: 'object',
      properties: {
        node_id: { type: 'string' },
        min_agg_pf: { type: 'number' },
        min_win_rate: { type: 'number' },
        max_drawdown: { type: 'number' },
        min_trade_count: { type: 'integer' },
        status: { type: 'string' },
      },
    },
  },
  {
    name: 'compare_versions',
    description: 'Diff two versions of the same node showing parameter changes and metric deltas.',
    input_schema: {
      type: 'object',
      properties: {
        version_a: { type: 'string' },
        version_b: { type: 'string' },
      },
      required: ['version_a', 'version_b'],
    },
  },
  {
    name: 'compare_compositions',
    description: 'Diff two compositions showing which node versions differ.',
    input_schema: {
      type: 'object',
      properties: {
        comp_a: { type: 'string' },
        comp_b: { type: 'string' },
      },
      required: ['comp_a', 'comp_b'],
    },
  },
  {
    name: 'list_experiments',
    description: 'List experiments with optional status filter.',
    input_schema: {
      type: 'object',
      properties: {
        status: { type: 'string', enum: ['planned', 'running', 'completed', 'abandoned'] },
      },
    },
  },
  {
    name: 'create_experiment',
    description: 'Create a new experiment with name and hypothesis.',
    input_schema: {
      type: 'object',
      properties: {
        name: { type: 'string' },
        hypothesis: { type: 'string' },
        experiment_code: { type: 'string' },
      },
      required: ['name'],
    },
  },
  {
    name: 'register_sweep',
    description: 'Register a parameter sweep for a node. Pre-allocates version IDs.',
    input_schema: {
      type: 'object',
      properties: {
        node_id: { type: 'string' },
        swept_parameter: { type: 'string' },
        value_range: { type: 'array', items: { type: 'number' } },
        experiment_name: { type: 'string' },
      },
      required: ['node_id', 'swept_parameter', 'value_range'],
    },
  },
]

// Map tool calls to API endpoints
async function executeTool(toolName, toolInput, apiGet, apiPost) {
  switch (toolName) {
    case 'get_node_versions': {
      const params = new URLSearchParams()
      if (toolInput.limit) params.set('limit', toolInput.limit)
      return await apiGet(`/v1/meta/node/${toolInput.node_id}/versions?${params}`)
    }
    case 'get_composition':
      return await apiGet(`/v1/meta/composition/${toolInput.composition_id}`)
    case 'get_experiment':
      return await apiGet(`/v1/meta/experiment/${toolInput.experiment_id}`)
    case 'search_versions': {
      const params = new URLSearchParams()
      Object.entries(toolInput).forEach(([k, v]) => {
        if (v != null) params.set(k, v)
      })
      return await apiGet(`/v1/meta/search/versions?${params}`)
    }
    case 'compare_versions':
      return await apiGet(`/v1/meta/compare/versions/${toolInput.version_a}/${toolInput.version_b}`)
    case 'compare_compositions':
      return await apiGet(`/v1/meta/compare/compositions/${toolInput.comp_a}/${toolInput.comp_b}`)
    case 'list_experiments': {
      const params = new URLSearchParams()
      if (toolInput.status) params.set('status', toolInput.status)
      return await apiGet(`/v1/meta/experiments?${params}`)
    }
    case 'create_experiment':
      return await apiPost('/v1/meta/experiment', toolInput)
    case 'register_sweep':
      return await apiPost('/v1/meta/sweep/register', toolInput)
    default:
      return { error: `Unknown tool: ${toolName}` }
  }
}

export default function ChatPanel({ selectedNode, topologyData, onClose, onNodeSelect }) {
  const { get: apiGet, post: apiPost } = useApi()
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [apiKeyInput, setApiKeyInput] = useState(() => localStorage.getItem('mcas_anthropic_key') || '')
  const messagesEndRef = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const anthropicKey = apiKeyInput || localStorage.getItem('mcas_anthropic_key')

  function buildSystemPrompt() {
    let system = `You are the MCAS Pipeline Introspection assistant. You help users understand and navigate the MCAS trading pipeline — its nodes, versions, experiments, and compositions.

You have access to tools that query the pipeline metadata database. Use them to answer questions about node versions, experiments, parameter sweeps, and compositions.

When referencing specific nodes, use the format [node_name](node:node_id) so the UI can make them clickable.`

    if (selectedNode) {
      system += `\n\nCurrently selected node:\n${JSON.stringify({
        id: selectedNode.id,
        name: selectedNode.name,
        type: selectedNode.type,
        subsystem: selectedNode.subsystem,
        description: selectedNode.description,
        current_version: selectedNode.current_version,
        version_count: selectedNode.version_count,
      }, null, 2)}`
    }

    if (topologyData) {
      const nodeList = (topologyData.nodes || []).map(n => ({
        id: n.id, name: n.name, subsystem: n.subsystem, type: n.type,
        version_count: n.version_count,
        agg_pf: n.current_version?.agg_pf,
      }))
      system += `\n\nPipeline nodes:\n${JSON.stringify(nodeList, null, 2)}`
    }

    return system
  }

  async function handleSend() {
    if (!input.trim() || loading) return
    if (!anthropicKey) return

    const userMsg = { role: 'user', content: input.trim() }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setInput('')
    setLoading(true)

    try {
      // Build conversation for API
      const apiMessages = newMessages
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => ({
          role: m.role,
          content: m.content,
        }))

      // Call Anthropic API
      const response = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': anthropicKey,
          'anthropic-version': '2023-06-01',
          'anthropic-dangerous-direct-browser-access': 'true',
        },
        body: JSON.stringify({
          model: 'claude-sonnet-4-20250514',
          max_tokens: 4096,
          system: buildSystemPrompt(),
          tools: TOOL_DEFINITIONS,
          messages: apiMessages,
        }),
      })

      if (!response.ok) {
        const errText = await response.text()
        throw new Error(`API error ${response.status}: ${errText.slice(0, 200)}`)
      }

      const data = await response.json()

      // Process response — handle tool use
      let textContent = ''
      let toolCalls = []

      for (const block of data.content) {
        if (block.type === 'text') {
          textContent += block.text
        } else if (block.type === 'tool_use') {
          toolCalls.push(block)
        }
      }

      // If there are tool calls, execute them and get final response
      if (toolCalls.length > 0) {
        const toolResults = []
        for (const tc of toolCalls) {
          try {
            const result = await executeTool(tc.name, tc.input, apiGet, apiPost)
            toolResults.push({
              type: 'tool_result',
              tool_use_id: tc.id,
              content: JSON.stringify(result).slice(0, 8000),
            })
          } catch (err) {
            toolResults.push({
              type: 'tool_result',
              tool_use_id: tc.id,
              content: JSON.stringify({ error: err.message }),
              is_error: true,
            })
          }
        }

        // Second call with tool results
        const followupMessages = [
          ...apiMessages,
          { role: 'assistant', content: data.content },
          { role: 'user', content: toolResults },
        ]

        const followupResp = await fetch('https://api.anthropic.com/v1/messages', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': anthropicKey,
            'anthropic-version': '2023-06-01',
            'anthropic-dangerous-direct-browser-access': 'true',
          },
          body: JSON.stringify({
            model: 'claude-sonnet-4-20250514',
            max_tokens: 4096,
            system: buildSystemPrompt(),
            tools: TOOL_DEFINITIONS,
            messages: followupMessages,
          }),
        })

        if (followupResp.ok) {
          const followupData = await followupResp.json()
          textContent = followupData.content
            .filter(b => b.type === 'text')
            .map(b => b.text)
            .join('')
        }

        // Show tool indicator
        const toolNames = toolCalls.map(tc => tc.name).join(', ')
        setMessages(prev => [
          ...prev,
          { role: 'system', content: `Queried: ${toolNames}`, type: 'tool_indicator' },
        ])
      }

      if (textContent) {
        setMessages(prev => [...prev, { role: 'assistant', content: textContent }])
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'system', content: `Error: ${err.message}`, type: 'error' }])
    } finally {
      setLoading(false)
    }
  }

  // Parse node references in text: [name](node:uuid)
  function renderContent(text) {
    if (!text) return null
    const parts = text.split(/(\[.*?\]\(node:[a-f0-9-]+\))/g)
    return parts.map((part, i) => {
      const match = part.match(/\[(.*?)\]\(node:([a-f0-9-]+)\)/)
      if (match) {
        return (
          <button
            key={i}
            onClick={() => onNodeSelect?.(match[2])}
            className="text-amber-400 hover:text-amber-300 underline underline-offset-2 transition-colors"
          >
            {match[1]}
          </button>
        )
      }
      return <span key={i}>{part}</span>
    })
  }

  // API key setup
  if (!anthropicKey) {
    return (
      <div className="fixed right-0 top-0 h-full w-[400px] bg-terminal-800 border-l border-terminal-600 shadow-xl z-50 flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-terminal-600">
          <h2 className="text-sm font-semibold text-terminal-100">AI Chat</h2>
          <button onClick={onClose} className="p-1 text-terminal-400 hover:text-terminal-100">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 flex items-center justify-center px-6">
          <div className="space-y-3 w-full">
            <p className="text-xs text-terminal-400 text-center">Enter your Anthropic API key to enable AI chat</p>
            <input
              type="password"
              value={apiKeyInput}
              onChange={e => setApiKeyInput(e.target.value)}
              placeholder="sk-ant-..."
              className="w-full px-3 py-2 bg-terminal-900 border border-terminal-600 rounded text-xs text-terminal-200 placeholder:text-terminal-600 focus:outline-none focus:border-amber-500"
            />
            <button
              onClick={() => {
                localStorage.setItem('mcas_anthropic_key', apiKeyInput)
                setApiKeyInput(apiKeyInput) // force re-render
              }}
              disabled={!apiKeyInput.startsWith('sk-')}
              className="w-full px-3 py-2 bg-amber-600 hover:bg-amber-500 disabled:bg-terminal-700 disabled:text-terminal-500 text-white text-xs font-medium rounded transition-colors"
            >
              Save Key
            </button>
            <p className="text-[10px] text-terminal-500 text-center">Key stored in localStorage only. Never sent to MCAS backend.</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed right-0 top-0 h-full w-[400px] bg-terminal-800 border-l border-terminal-600 shadow-xl z-50 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-terminal-600">
        <div>
          <h2 className="text-sm font-semibold text-terminal-100">AI Chat</h2>
          {selectedNode && (
            <p className="text-[10px] text-terminal-400">Context: {selectedNode.name}</p>
          )}
        </div>
        <button onClick={onClose} className="p-1 text-terminal-400 hover:text-terminal-100">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && (
          <div className="text-xs text-terminal-500 text-center py-8">
            <p className="mb-2">Ask about the pipeline, nodes, versions, or experiments.</p>
            <div className="space-y-1 text-terminal-600">
              <p>"Why did we change this node?"</p>
              <p>"What experiments are running?"</p>
              <p>"Compare the last two versions"</p>
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`text-xs ${
            msg.role === 'user' ? 'text-right' :
            msg.type === 'tool_indicator' ? 'text-center' :
            msg.type === 'error' ? 'text-center' :
            'text-left'
          }`}>
            {msg.type === 'tool_indicator' ? (
              <span className="inline-block px-2 py-1 bg-terminal-900 rounded text-terminal-500 text-[10px]">
                {msg.content}
              </span>
            ) : msg.type === 'error' ? (
              <span className="inline-block px-2 py-1 bg-red-900/30 rounded text-red-400 text-[10px]">
                {msg.content}
              </span>
            ) : (
              <div className={`inline-block max-w-[85%] px-3 py-2 rounded-lg ${
                msg.role === 'user'
                  ? 'bg-amber-600/20 text-terminal-200'
                  : 'bg-terminal-900 text-terminal-300'
              }`}>
                <div className="whitespace-pre-wrap">{renderContent(msg.content)}</div>
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex items-center gap-2 text-xs text-terminal-500">
            <Loader2 className="w-3 h-3 animate-spin" />
            Thinking...
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-terminal-600 p-3">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
            placeholder="Ask about the pipeline..."
            disabled={loading}
            className="flex-1 px-3 py-2 bg-terminal-900 border border-terminal-600 rounded text-xs text-terminal-200 placeholder:text-terminal-600 focus:outline-none focus:border-amber-500 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="p-2 bg-amber-600 hover:bg-amber-500 disabled:bg-terminal-700 rounded transition-colors"
          >
            <Send className="w-3.5 h-3.5 text-white" />
          </button>
        </div>
      </div>
    </div>
  )
}
