import { useInnerLifeStore } from "../../stores/innerLifeStore";
import Panel from "../layout/Panel";
import AestheticChart from "./AestheticChart";
import BigFiveChart from "./BigFiveChart";
import EnergyBar from "./EnergyBar";
import ValenceArousalPlot from "./ValenceArousalPlot";
import ValuesChart from "./ValuesChart";

// 内在状态面板容器（04 §1）：读 innerLifeStore.current；current===null 时整体占位「等待核心服务连接…」
// （§6，不渲染子组件防 undefined 崩）；error 非 null 在面板顶部红字一行；各子组件只收它需要的字段。
export default function InnerStatePanel() {
  const current = useInnerLifeStore((s) => s.current);
  const error = useInnerLifeStore((s) => s.error);

  return (
    <Panel title="内在状态">
      {error !== null && <p className="error-text inner-state-panel__error">{error}</p>}
      {current === null ? (
        "等待核心服务连接…"
      ) : (
        <div className="inner-state-panel__body">
          <ValenceArousalPlot valence={current.valence} arousal={current.arousal} />
          <EnergyBar energy={current.energy} energy_state={current.energy_state} />
          <h3 className="panel-section-title">性格</h3>
          <BigFiveChart personality={current.personality} />
          <h3 className="panel-section-title">三观</h3>
          <ValuesChart values={current.values} />
          <h3 className="panel-section-title">审美</h3>
          <AestheticChart aesthetic={current.aesthetic} />
        </div>
      )}
    </Panel>
  );
}
