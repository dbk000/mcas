# MCAS Frontend Sitemap

This flowchart represents the current routing architecture and trust boundaries of the MCAS Frontend as defined in `App.jsx`.

```mermaid
graph TD
    %% Public Funnel
    subgraph Public_Funnel [Public Funnel - MarketingLayout]
        L[Landing /] --> W[Winners /winners]
        L --> P[Pricing /pricing]
        L --> S[Settings /settings]
        P --> U[Upgrade /upgrade]
        U --> CS[Checkout Success]
        U --> CC[Checkout Cancel]
    end

    %% The Gate
    Public_Funnel -- "Paid Access" --> Gate{Paid Gate}
    
    %% Protected App Shell
    subgraph Operator_App [Operator App - Layout]
        Gate --> Dash[Dashboard /app]
        Dash --> Strat[Strategies /app/strategies]
        
        subgraph Opportunity_Core [Opportunity Analysis]
            Opp[Opportunities /app/opportunities]
            Opp --> Det[Detail /app/opportunities/:id]
            Opp --> Surv[Survey /app/opportunities/survey]
        end
        
        subgraph Market_Intel [Market Intelligence]
            Mkt[Markets /app/markets]
            Mkt --> MDet[Symbol Detail /app/markets/:symbol]
            Pipe[Pipeline /app/pipeline]
        end
        
        subgraph Backtesting_Control [Simulation & Control]
            Runs[Runs /app/runs]
            Runs --> RDet[Run Detail /app/runs/:id]
            Runs --> RComp[Compare /app/runs/compare]
            RDet --> Sim[Sim Results /app/runs/:id/simulation]
            Ctrl[Control Plane /app/control]
            Paper[Paper Trading /app/paper]
        end

        subgraph Advanced_Analytics [Advanced]
            Corr[Correlations /app/analytics/correlations]
        end
    end

    %% Logic Connections
    Dash -.-> Opp
    Dash -.-> Runs
    Strat -.-> Det
    Dash -.-> Pipe
```

### Zone Definitions

1.  **Public Funnel:** Marketing surfaces that consume static/curated JSON from `/public/curation/`.
2.  **The Gate:** A `PaidRoute` wrapper that checks local browser activation state.
3.  **Operator App:** The core interactive surface requiring a valid API base URL and session.
