import { cloneElement, useCallback, useState } from 'react';

/**
 * FeedbackOnset: A low-latency shim for visual acknowledgement.
 * Ensures <50ms visual response to critical operator actions.
 * Supports both onClick handlers and native form submission acknowledgement.
 */
export const FeedbackOnset = ({ 
  children, 
  onClick, 
  onActionStart, 
  onActionComplete,
  latencyLabel = 'action'
}) => {
  const [isPending, setIsPending] = useState(false);

  const triggerAction = useCallback(async (event) => {
    // Record onset start (visual)
    const now = performance.now();
    setIsPending(true);

    if (onActionStart) onActionStart(now);

    try {
      if (onClick) {
        // If onClick returns a promise, wait for it
        const result = onClick(event);
        if (result instanceof Promise) {
          await result;
        }
      }
    } catch (err) {
      console.error(`FeedbackOnset [${latencyLabel}] error:`, err);
    } finally {
      // NOTE: For navigations, this component will unmount before setIsPending(false)
      // This is expected and provides the "pending until gone" UX.
      setIsPending(false);
      const end = performance.now();
      if (onActionComplete) onActionComplete(end - now);
    }
  }, [onClick, onActionStart, onActionComplete, latencyLabel]);

  return (
    <div 
      className="relative inline-block w-full" 
      data-latency-label={latencyLabel}
      data-is-pending={isPending}
    >
      {cloneElement(children, {
        onClick: (e) => {
          triggerAction(e);
          if (children.props.onClick) children.props.onClick(e);
        },
        disabled: isPending || children.props.disabled,
      })}
      
      {/* Micro-feedback indicator for Operator Standard */}
      {isPending && (
        <div 
          className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center"
          data-testid="feedback-onset-spinner"
        >
          <div className="size-2 animate-pulse rounded-full bg-blue-500" />
        </div>
      )}
    </div>
  );
};
