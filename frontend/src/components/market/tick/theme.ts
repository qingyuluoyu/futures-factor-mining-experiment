import { useDisplayStore } from "@/stores/display"
import { readChartTheme } from "@/lib/chart-theme"

export const getTheme = (): string => useDisplayStore.getState().theme
export function useTheme(): string {
  return useDisplayStore((state) => `${state.theme}:${state.candleUp}:${state.candleDown}`)
}
export function stockCandleColors(): { bull: string; bear: string; bullAlpha: string; bearAlpha: string } {
  const state = useDisplayStore.getState()
  return { bull: state.candleUp, bear: state.candleDown, bullAlpha: state.candleUp, bearAlpha: state.candleDown }
}
export function chartTheme(_theme: string) {
  const current = readChartTheme()
  return { ...current, textStrong: current.text, crosshairLabelBg: current.crosshairLabel,
    tooltipText: current.text, tooltipBorder: current.border, infoBarBg: current.background, zoomFill: current.gridSoft }
}
