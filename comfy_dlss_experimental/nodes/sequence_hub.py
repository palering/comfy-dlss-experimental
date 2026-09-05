from comfy_api.latest import io

from .types import TemporalSequence


class DLSSExperimentalSequenceHub(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalSequenceHub",
            display_name="DLSS Prepared Sequence Hub",
            category="DLSS Experimental/Utilities",
            search_aliases=["dlss branch", "video hub", "sequence splitter"],
            description=(
                "Routes one prepared video sequence to four independent graph branches. "
                "It does not copy frames, rebuild guides, render NR, or make GPU work concurrent; "
                "all outputs reference the same cached input preparation."
            ),
            inputs=[
                TemporalSequence.Input(
                    "sequence",
                    display_name="已准备的视频输入",
                    tooltip="连接 Video Input Adapter 的输出；该节点只做接线整理，不执行视频处理。",
                )
            ],
            outputs=[
                TemporalSequence.Output("branch_1"),
                TemporalSequence.Output("branch_2"),
                TemporalSequence.Output("branch_3"),
                TemporalSequence.Output("branch_4"),
            ],
            is_experimental=True,
        )

    @classmethod
    def execute(cls, sequence):
        # Deliberately return the same immutable-by-contract descriptor. The
        # expensive prepared color/motion data lives in a shared disk cache;
        # fan-out must not imply four copies or four preparation jobs.
        return io.NodeOutput(sequence, sequence, sequence, sequence)
