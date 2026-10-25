"use client"

import { StockMembers } from "./stock-board-browser"

export function StockUniverse({ activeCode, onSelect }: { activeCode: string; onSelect: (code: string) => void }): React.JSX.Element {
  return <div className="flex h-full min-h-[20rem] flex-col overflow-auto p-3"><StockMembers compact activeCode={activeCode} onSelect={onSelect} /></div>
}
