import { useInnerLifeStore } from "../../stores/innerLifeStore";
import Panel from "../layout/Panel";
import EnergyBar from "./EnergyBar";
import ValenceArousalPlot from "./ValenceArousalPlot";
import { INNER_SECTIONS } from "../../lib/innerDocument";
import FlowPages from "../layout/FlowPages";

export default function InnerStatePanel() {
  const current = useInnerLifeStore((s) => s.current);
  const error = useInnerLifeStore((s) => s.error);
  return (
    <Panel title="内在状态" className="inner-state-panel paged-panel">
      {error !== null && <p className="error-text" role="alert">{error}</p>}
      {current === null ? "等待核心服务连接…" : <FlowPages><div className="inner-state-panel__body">
        <div className="inner-state-panel__top">
          <ValenceArousalPlot valence={current.valence} arousal={current.arousal} />
          <EnergyBar energy={current.energy} energy_state={current.energy_state} />
        </div>
        <article aria-label="当前内在文档">
          {Object.entries(INNER_SECTIONS).map(([section, label]) => {
            const topics = current.inner_document.topics.filter((t) => t.section === section);
            return topics.length > 0 && <section key={section} className="inner-state-panel__section">
              <h3 className="panel-section-title">{label}</h3>
              {topics.map((topic) => <div key={topic.id}>
                <h4>{topic.title}</h4>
                <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{topic.content}</p>
              </div>)}
            </section>;
          })}
        </article>
      </div></FlowPages>}
    </Panel>
  );
}
