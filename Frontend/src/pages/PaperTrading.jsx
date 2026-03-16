import { useState } from 'react'
import { Wallet, Target, Clock, BarChart2 } from 'lucide-react'
import { useTradingAccount } from '../hooks/useData'
import {
  Card,
  CardHeader,
  CardContent,
  RefreshControl,
  Tabs,
} from '../components/ui'
import { formatRelativeTime } from '../utils/formatters'
import AccountOverview from './paper-trading/AccountOverview'
import PositionsPanel from './paper-trading/PositionsPanel'
import TradesPanel from './paper-trading/TradesPanel'
import PerformancePanel from './paper-trading/PerformancePanel'

const PAPER_TRADING_TAB_ID = 'paper-trading-tabs'

export default function PaperTrading() {
  const [activeTab, setActiveTab] = useState('account')
  const { data: accountData, refetch: refetchAccount } = useTradingAccount()

  const isActive = accountData?.active || false
  const account = accountData?.account || {}
  const performance = accountData?.performance || {}
  const tabs = [
    { value: 'account', label: 'Account', icon: Wallet },
    { value: 'positions', label: 'Positions', icon: Target },
    { value: 'trades', label: 'Trades', icon: Clock },
    { value: 'performance', label: 'Performance', icon: BarChart2 },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-terminal-100">Paper Trading</h1>
            {isActive ? (
              <span className="flex items-center gap-1.5 rounded-full bg-green-500/20 px-2 py-0.5 text-sm text-green-400">
                <span className="size-1.5 animate-pulse rounded-full bg-green-400" />
                Active
              </span>
            ) : (
              <span className="rounded-full bg-terminal-700 px-2 py-0.5 text-sm text-terminal-400">
                Inactive
              </span>
            )}
          </div>
          {isActive && accountData?.run_name && (
            <p className="text-terminal-400">
              Session: <span className="font-mono text-terminal-300">{accountData.run_name}</span>
              {accountData.started_at && (
                <span className="ml-2 text-terminal-500">
                  (started {formatRelativeTime(accountData.started_at)})
                </span>
              )}
            </p>
          )}
        </div>

        <RefreshControl
          lastUpdated={null}
          onRefresh={refetchAccount}
        />
      </div>

      <Card>
        <CardHeader className="border-b border-terminal-700">
          <Tabs
            tabs={tabs}
            activeTab={activeTab}
            onChange={setActiveTab}
            idBase={PAPER_TRADING_TAB_ID}
          />
        </CardHeader>
        <CardContent className="pt-6">
          <div
            role="tabpanel"
            id={`${PAPER_TRADING_TAB_ID}-panel-${activeTab}`}
            aria-labelledby={`${PAPER_TRADING_TAB_ID}-tab-${activeTab}`}
          >
            {activeTab === 'account' && (
              <AccountOverview
                account={account}
                performance={performance}
                isActive={isActive}
              />
            )}
            {activeTab === 'positions' && <PositionsPanel isActive={isActive} />}
            {activeTab === 'trades' && <TradesPanel isActive={isActive} />}
            {activeTab === 'performance' && <PerformancePanel isActive={isActive} />}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
