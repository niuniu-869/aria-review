export const sampleFullMarkdown = `# Deep Learning Approaches for Bibliometric Network Analysis

Jane Doe, Richard Roe, and Mary Major

## Abstract

This study investigates the application of graph neural networks to large-scale bibliometric citation networks. We propose a scalable embedding method that captures both structural and semantic signals from scholarly corpora, and we evaluate it across three benchmark datasets covering the period from 2010 to 2022.

## 1 Introduction

Bibliometric analysis has become a cornerstone of research evaluation, enabling scholars to map the intellectual structure of a field. Traditional approaches rely on co-citation and bibliographic coupling, but they struggle to scale to corpora containing millions of documents. Recent advances in representation learning offer a promising alternative.

In this paper we develop a unified framework that combines citation topology with textual abstracts. Our contributions are threefold: a new sampling strategy, a hybrid loss function, and an empirical comparison against four established baselines.

## 2 Methods

We model each corpus as a directed graph in which nodes represent documents and edges represent citations. Node features are derived from TF-IDF vectors of the abstract text, while edge weights encode the recency of each citation. The encoder is a two-layer attention network trained with a contrastive objective.

The following table summarizes the three benchmark datasets used in our experiments and their key descriptive statistics.

<table><tr><td>Benchmark Dataset Summary</td><td>Documents</td><td colspan="2">Coverage</td></tr><tr><td>BiblioNet</td><td>120000</td><td>2010</td><td>2018</td></tr><tr><td>CiteSeerX</td><td>340000</td><td>2012</td><td>2022</td></tr></table>

## 3 Results

Across all three datasets our method improves link-prediction accuracy by an average of 7.4 percentage points over the strongest baseline. The gains are most pronounced on the sparsest graph, where structural signals alone are insufficient and the textual channel contributes substantially.

## 4 Conclusion

We have presented a scalable embedding approach for bibliometric networks that jointly exploits citation structure and document semantics. Future work will explore temporal dynamics and cross-lingual corpora.
`;

export const sampleStructure = {
  paper_id: 10,
  attachment_id: 50,
  page_count: 2,
  blocks: [
    { block_idx: 0, type: "title", text_level: 1, page_no: 1, md_line_start: 1, md_line_end: 1, bbox: [72, 90, 523, 120], section_title: "Deep Learning Approaches for Bibliometric Network Analysis", text_preview: "Deep Learning Approaches for Bibliometric Network Analysis" },
    { block_idx: 3, type: "text", text_level: null, page_no: 1, md_line_start: 7, md_line_end: 7, bbox: [72, 185, 523, 250], section_title: "Abstract", text_preview: "This study investigates the application of graph neural networks to large-scale bibliometric citation networks." },
    { block_idx: 12, type: "text", text_level: null, page_no: 2, md_line_start: 25, md_line_end: 25, bbox: [72, 340, 523, 420], section_title: "3 Results", text_preview: "Across all three datasets our method improves link-prediction accuracy by an average of 7.4 percentage points." },
  ],
  tables: [
    {
      table_idx: 0,
      block_idx: 10,
      page_no: 2,
      bbox: [72, 200, 523, 300],
      n_rows: 3,
      n_cols: 4,
      grid: [
        ["Benchmark Dataset Summary", "Documents", "Coverage", ""],
        ["BiblioNet", "120000", "2010", "2018"],
        ["CiteSeerX", "340000", "2012", "2022"],
      ],
      caption: "",
    },
  ],
  has_bbox: true,
  markdown_sha256: "e59784e136466e3f1f626475d9d24812e817756fdca486e735561b9bdf5669fb",
  schema_version: 1,
  source_pdf_sha256: "0".repeat(64),
  bbox_coord_space: "mineru_1000",
  page_width: null,
  page_height: null,
  rotation: null,
} as const;

export const sampleReviewWithProvenance = {
  review_md:
    "## Evidence-backed review\n\nThe method improves link prediction [[anchor:a10_12_0__occ0]][1][[/anchor]], and the text channel helps most on sparse graphs [[anchor:a10_12_1__occ1]][1][[/anchor]].",
  provenance_map: {
    a10_12_0__occ0: {
      paper_id: 10,
      attachment_id: 50,
      page_no: 2,
      block_idx: 12,
      bbox: [72, 340, 523, 420],
      table_idx: null,
      cell_row: null,
      cell_col: null,
      section_title: "3 Results",
      quote: "Across all three datasets our method improves link-prediction accuracy by an average of 7.4 percentage points over the strongest baseline.",
    },
    a10_12_1__occ1: {
      paper_id: 10,
      attachment_id: 50,
      page_no: 2,
      block_idx: 12,
      bbox: [72, 340, 523, 420],
      table_idx: null,
      cell_row: null,
      cell_col: null,
      section_title: "3 Results",
      quote: "The gains are most pronounced on the sparsest graph, where structural signals alone are insufficient and the textual channel contributes substantially.",
    },
  },
} as const;
