import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { ALL_SEARCH_RESULTS, SEARCH_CATEGORIES, RECENT_SEARCHES, type SearchResultItem } from '@/mocks/search';

interface GlobalSearchProps {
  isOpen: boolean;
  onClose: () => void;
}

export function GlobalSearch({ isOpen, onClose }: GlobalSearchProps) {
  // Client-side navigation: assigning window.location reloaded the whole SPA,
  // so opening a search result reset the app and re-fetched every route chunk.
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const filteredResults = useMemo(() => {
    if (!query.trim()) return [];
    const q = query.toLowerCase();
    return ALL_SEARCH_RESULTS.filter(
      r =>
        r.label.toLowerCase().includes(q) ||
        r.subtitle.toLowerCase().includes(q) ||
        r.categoryLabel.toLowerCase().includes(q)
    );
  }, [query]);

  const groupedResults = useMemo(() => {
    const groups: { category: string; label: string; icon: string; items: SearchResultItem[] }[] = [];
    const seen = new Set<string>();

    for (const r of filteredResults) {
      if (!seen.has(r.category)) {
        seen.add(r.category);
        const cat = SEARCH_CATEGORIES.find(c => c.slug === r.category);
        groups.push({
          category: r.category,
          label: cat?.label || r.categoryLabel,
          icon: cat?.icon || 'ri-file-line',
          items: [],
        });
      }
      const group = groups.find(g => g.category === r.category);
      if (group) group.items.push(r);
    }

    return groups;
  }, [filteredResults]);

  const allFlat = useMemo(() => {
    const flat: SearchResultItem[] = [];
    for (const g of groupedResults) {
      flat.push(...g.items);
    }
    return flat;
  }, [groupedResults]);

  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const total = allFlat.length;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex(prev => (prev + 1) % Math.max(total, 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex(prev => (prev - 1 + Math.max(total, 1)) % Math.max(total, 1));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (allFlat[selectedIndex]) {
          navigate(allFlat[selectedIndex].href);
          onClose();
        }
      } else if (e.key === 'Escape') {
        onClose();
      }
    },
    [allFlat, selectedIndex, onClose, navigate]
  );

  useEffect(() => {
    if (listRef.current && selectedIndex >= 0) {
      const el = listRef.current.querySelector(`[data-search-index="${selectedIndex}"]`);
      if (el) el.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex]);

  useEffect(() => {
    function handleGlobalKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        if (isOpen) onClose();
        else {
          // Will be toggled by parent
        }
      }
    }
    document.addEventListener('keydown', handleGlobalKey);
    return () => document.removeEventListener('keydown', handleGlobalKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const showRecent = !query.trim() && RECENT_SEARCHES.length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-foreground-950/40 backdrop-blur-sm" onClick={onClose}></div>

      {/* Modal */}
      <div className="relative w-full max-w-[640px] bg-background-50 rounded-2xl border border-background-200 shadow-2xl shadow-foreground-950/10 overflow-hidden animate-in fade-in zoom-in-95">
      {/* Search input */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-background-100">
          <span className="w-8 h-8 rounded-lg bg-primary-100 flex items-center justify-center shrink-0">
            <AppIcon className="ri-search-line text-base text-primary-600"></AppIcon>
          </span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search learners, employers, evidence, KSBs, reports..."
            className="flex-1 bg-transparent text-[15px] text-foreground-900 placeholder:text-foreground-300 outline-none border-none focus:ring-0 font-body"
          />
          {query && (
            <button
              onClick={() => setQuery('')}
              className="w-6 h-6 rounded-md flex items-center justify-center text-foreground-300 hover:text-foreground-500 hover:bg-background-100 transition-smooth cursor-pointer"
            >
              <AppIcon className="ri-close-line text-sm"></AppIcon>
            </button>
          )}
          <span className="text-[11px] text-foreground-300 bg-background-100 px-2 py-0.5 rounded-md font-medium border border-foreground-200">
            ESC
          </span>
        </div>

        {/* Results area */}
        <div ref={listRef} className="max-h-[420px] overflow-y-auto py-2">
          {showRecent && (
            <div className="pb-1">
              <div className="px-5 py-1.5">
                <span className="text-[10px] font-semibold text-foreground-300 uppercase tracking-widest">Recent Searches</span>
              </div>
              {RECENT_SEARCHES.map((s, i) => (
                <button
                  key={i}
                  className="w-full flex items-center gap-3 px-5 py-2.5 text-left hover:bg-background-100 transition-smooth"
                  onClick={() => { setQuery(s); inputRef.current?.focus(); }}
                >
                  <AppIcon className="ri-history-line text-foreground-300 text-sm"></AppIcon>
                  <span className="text-[14px] text-foreground-600">{s}</span>
                </button>
              ))}
            </div>
          )}

          {!showRecent && query.trim() && groupedResults.length === 0 && (
            <div className="px-5 py-10 text-center">
              <div className="w-12 h-12 rounded-full bg-background-100 flex items-center justify-center mx-auto mb-3">
                <AppIcon className="ri-search-line text-xl text-foreground-300"></AppIcon>
              </div>
              <p className="text-sm text-foreground-500 font-medium">No results found</p>
              <p className="text-xs text-foreground-300 mt-1">Try a different search term</p>
            </div>
          )}

          {!showRecent && groupedResults.map(group => (
            <div key={group.category} className="pb-0.5">
              <div className="px-5 py-1.5 flex items-center gap-2">
                <AppIcon className={`${group.icon} text-[10px] text-foreground-300`}></AppIcon>
                <span className="text-[10px] font-semibold text-foreground-300 uppercase tracking-widest">{group.label}</span>
                <span className="text-[10px] text-foreground-250 ml-auto">{group.items.length}</span>
              </div>
              {group.items.map((item) => {
                const flatIdx = allFlat.findIndex(f => f.id === item.id);
                const isSelected = flatIdx === selectedIndex;

                return (
                  <a
                    key={item.id}
                    href={item.href}
                    data-search-index={flatIdx}
                    onClick={e => { e.preventDefault(); navigate(item.href); onClose(); }}
                    className={`flex items-center gap-3 px-5 py-2.5 transition-smooth group ${
                      isSelected ? 'bg-primary-50/70 border-l-2 border-primary-400' : 'hover:bg-background-100 border-l-2 border-transparent'
                    }`}
                  >
                    <span className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                      isSelected ? 'bg-primary-100 text-primary-600' : 'bg-background-100 text-foreground-400 group-hover:bg-background-200/70'
                    }`}>
                      <AppIcon className={`${item.icon} text-sm`}></AppIcon>
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className={`text-[14px] truncate ${isSelected ? 'text-primary-800 font-medium' : 'text-foreground-800'}`}>
                        {item.label}
                      </p>
                      <p className="text-[11px] text-foreground-400 truncate">{item.subtitle}</p>
                    </div>
                    {item.statusBadge && (
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${
                        item.statusBadge === 'On Track' || item.statusBadge === 'Validated' || item.statusBadge === 'Completed' || item.statusBadge === 'Resolved' || item.statusBadge === 'Active'
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-200/50'
                          : item.statusBadge === 'At Risk' || item.statusBadge === 'Overdue' || item.statusBadge === 'Rejected'
                          ? 'bg-red-50 text-red-700 border border-red-200/50'
                          : item.statusBadge === 'Pending' || item.statusBadge === 'Awaiting' || item.statusBadge === 'Scheduled' || item.statusBadge === 'In Progress'
                          ? 'bg-amber-50 text-amber-700 border border-amber-200/50'
                          : 'bg-secondary-50 text-secondary-700 border border-secondary-200/50'
                      }`}>
                        {item.statusBadge}
                      </span>
                    )}
                  </a>
                );
              })}
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-4 px-5 py-2.5 border-t border-background-100 bg-background-50">
          <div className="flex items-center gap-1.5 text-[11px] text-foreground-300">
            <span className="bg-background-100 px-1.5 py-0.5 rounded text-foreground-400 font-medium border border-foreground-200">↑↓</span>
            <span>Navigate</span>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-foreground-300">
            <span className="bg-background-100 px-1.5 py-0.5 rounded text-foreground-400 font-medium border border-foreground-200">↵</span>
            <span>Open</span>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-foreground-300">
            <span className="bg-background-100 px-1.5 py-0.5 rounded text-foreground-400 font-medium border border-foreground-200">Esc</span>
            <span>Close</span>
          </div>
        </div>
      </div>
    </div>
  );
}