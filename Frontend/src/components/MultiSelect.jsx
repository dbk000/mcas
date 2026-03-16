import { useEffect, useId, useRef, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

export default function MultiSelect({
  options,
  value = [],
  onChange,
  className = '',
  placeholder = 'All',
  ariaLabel,
}) {
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const generatedId = useId().replace(/:/g, '')
  const buttonId = `multi-select-${generatedId}-button`
  const listboxId = `multi-select-${generatedId}-listbox`
  const getOptionId = (optionValue) => `multi-select-${generatedId}-option-${optionValue}`
  const buttonRef = useRef(null)
  const optionRefs = useRef([])

  useEffect(() => {
    if (!open) {
      optionRefs.current = []
      return
    }

    optionRefs.current[activeIndex]?.focus()
  }, [activeIndex, open])

  const toggle = (optionValue) => {
    const nextValue = value.includes(optionValue)
      ? value.filter((item) => item !== optionValue)
      : [...value, optionValue]

    onChange(nextValue)
  }

  const closeMenu = () => {
    setOpen(false)
    window.requestAnimationFrame(() => {
      buttonRef.current?.focus()
    })
  }

  const openMenu = () => {
    if (!options.length) {
      return
    }

    const selectedIndex = options.findIndex((option) => value.includes(option.value))
    const nextIndex = selectedIndex >= 0 ? selectedIndex : 0
    setActiveIndex(nextIndex)
    setOpen(true)
  }

  const focusOption = (nextIndex) => {
    const boundedIndex = Math.max(0, Math.min(options.length - 1, nextIndex))
    setActiveIndex(boundedIndex)
    optionRefs.current[boundedIndex]?.focus()
  }

  const handleButtonKeyDown = (event) => {
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      closeMenu()
      return
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault()

      if (!open) {
        openMenu()
        return
      }

      focusOption((activeIndex + 1) % options.length)
      return
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault()

      if (!open) {
        openMenu()
        return
      }

      focusOption((activeIndex - 1 + options.length) % options.length)
      return
    }

    if (event.key === 'Home' && open) {
      event.preventDefault()
      focusOption(0)
      return
    }

    if (event.key === 'End' && open) {
      event.preventDefault()
      focusOption(options.length - 1)
      return
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()

      if (!open) {
        openMenu()
        return
      }

      const activeOption = options[activeIndex]
      if (activeOption) {
        toggle(activeOption.value)
      }
    }
  }

  const handleOptionKeyDown = (event, optionValue, index) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      focusOption((index + 1) % options.length)
      return
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault()
      focusOption((index - 1 + options.length) % options.length)
      return
    }

    if (event.key === 'Home') {
      event.preventDefault()
      focusOption(0)
      return
    }

    if (event.key === 'End') {
      event.preventDefault()
      focusOption(options.length - 1)
      return
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      toggle(optionValue)
      return
    }

    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closeMenu()
      return
    }

    if (event.key === 'Tab') {
      setOpen(false)
    }
  }

  const label = value.length === 0
    ? placeholder
    : value.length <= 2
      ? value.join(', ')
      : `${value.length} selected`

  return (
    <div className={`relative ${className}`}>
      <button
        id={buttonId}
        ref={buttonRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-activedescendant={open && options[activeIndex] ? getOptionId(options[activeIndex].value) : undefined}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={handleButtonKeyDown}
        className="flex w-full items-center justify-between rounded border border-terminal-500 bg-terminal-800 px-3 py-2 text-left text-terminal-100 focus:border-amber-500 focus:outline-none focus:ring-1 focus:ring-amber-500/20"
      >
        <span className="truncate">{label}</span>
        {open
          ? <ChevronUp className="size-4 shrink-0 text-terminal-400" />
          : <ChevronDown className="size-4 shrink-0 text-terminal-400" />
        }
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={closeMenu} />
          <div
            id={listboxId}
            role="listbox"
            aria-multiselectable="true"
            aria-labelledby={buttonId}
            className="absolute z-50 mt-1 max-h-48 w-full overflow-y-auto rounded border border-terminal-500 bg-terminal-800 shadow-lg"
          >
            {options.map((option, index) => (
              <button
                type="button"
                key={option.value}
                id={getOptionId(option.value)}
                role="option"
                aria-selected={value.includes(option.value)}
                ref={(element) => {
                  optionRefs.current[index] = element
                }}
                tabIndex={index === activeIndex ? 0 : -1}
                onFocus={() => setActiveIndex(index)}
                onKeyDown={(event) => handleOptionKeyDown(event, option.value, index)}
                onClick={() => toggle(option.value)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-terminal-100 hover:bg-terminal-700 focus:bg-terminal-700 focus:outline-none"
              >
                <span aria-hidden="true" className="inline-flex w-4 justify-center text-amber-400">
                  {value.includes(option.value) ? '✓' : ''}
                </span>
                <span>{option.label}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
