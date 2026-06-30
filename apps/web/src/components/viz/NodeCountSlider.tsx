/**
 * NodeCountSlider.tsx — 网络图 Top-N 节点数滑块
 *
 * 原生 range input + 当前值显示，走宣纸样式。
 * 数据源为后端 top100；本组件只负责选 N，切片由调用方在组件外按 strength 取前 N。
 */
export interface NodeCountSliderProps {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
  label?: string;
}

export function NodeCountSlider({
  value,
  min = 10,
  max = 100,
  step = 10,
  onChange,
  label = "节点数",
}: NodeCountSliderProps) {
  return (
    <label className="viz-slider">
      <span className="viz-slider-label">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={label}
      />
      <span className="viz-slider-value tnum">{value}</span>
    </label>
  );
}
