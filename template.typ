#set page(
  paper: "a4",
  margin: (top: 2.5cm, bottom: 2.5cm, left: 2.5cm, right: 2.5cm),
  header: context {
    let page-num = counter(page).get().first()
    if page-num >= 3 {
      align(center)[2025-2026(2) 《区块链技术及应用实验》课程实验报告]
    }
  },
)

#set text(font: ("SimSun", "Times New Roman"), size: 12pt)
#set par(first-line-indent: 2em, justify: true)
#set heading(numbering: "1.")
#set enum(numbering: "1.")

#let report-title = "实验十一：项目进展报告"

#let placeholderfigure(path, w, cap) = figure(
  image(path, width: w),
  caption: [#cap],
)

#let code-block(path, lang: "text") = block[
  #set par(first-line-indent: 0em)
  #text(weight: "bold")[源文件：#path]
  #raw(read(path), block: true, lang: lang)
]

#let code-snippet(path, start: 1, end: none, lang: "text", label: none) = {
  let lines = read(path).split("\n")
  let from = if start <= 1 { 0 } else { start - 1 }
  let to = if end == none or end > lines.len() { lines.len() } else { end }
  let body = lines.slice(from, to).join("\n")
  block[
    #set par(first-line-indent: 0em)
    #set text(size: 8.5pt)
    #text(weight: "bold")[#(if label == none { path } else { label })]
    #raw(body, block: true, lang: lang)
  ]
}

#block(width: 100%)[
  #place(
    bottom + left,
    dx: 0.37cm,
    dy: 0.5cm,
    image("figures/图片2.png", width: 2.62cm, height: 2.59cm),
  )
]

#align(center)[
  #image("figures/图片1.png", width: 12.04cm, height: 2.81cm)
]

#align(center)[
  #text(font: "SimHei", size: 24pt)[2025-2026学年第2学期]
]

#align(center)[
  #text(font: "SimHei", size: 26pt, weight: "bold")[《区块链技术及应用实验》]
]

#align(center)[
  #text(font: "SimHei", size: 22pt, weight: "bold")[课程实验报告]
]

#let info-row(label, value, width: 7cm) = grid(
  columns: (auto, width),
  column-gutter: 0.5cm,
  align: (right, center),

  [#label],
  [#stack(
    dir: ttb,
    spacing: 0.08em,
    align(center)[#value],
    line(length: width),
  )],
)

#align(center)[
  #stack(
    dir: ttb,
    spacing: 0.35cm,

    info-row([学    号：], [2023115323]),
    info-row([学生姓名：], [侯懿]),
    info-row([班    级：], [软件2023-02班]),
    info-row([教   师：], [赵其刚]),
  )
]

\
\
\
\
\
\
\
\
\
\
\
\
\
\
#align(center)[
  #text(font: "SimHei", size: 10.5pt)[评阅教师签字：]
]

#pagebreak()

#align(center)[
  #text(font: "SimHei", size: 15pt, weight: "bold")[关于区块链技术及应用实验报告的说明]
]

\
（1）电子版和纸质版均要交。\
（2）电子版提交后，实验验收通过者才收纸质版。\
（3）关于电子版，有以下规定：\
#set par(first-line-indent: 2em)
① 电子版解压缩后不出现乱码问题，文件保留核心可运行代码，文件大小不宜过大。\
#set par(first-line-indent: 2em)
② 必须用#text(rgb("#0000ff"))[本学期]发布的实验报告模板。\
#set par(first-line-indent: 2em)
③ 实验报告中的截图#text(rgb("#0000ff"))[应只截结果，不允许截代码。]\
#set par(first-line-indent: 2em)
④ #strong[截去截图中多余的部分]，并将截图的分辨率降低到能打印清楚即可。\
达不到上述要求者的纸质报告将被拒收！！！\
（4）纸质版必须#text(rgb("#0000ff"))[双面打印。]\
（5）电子版及纸质版中的格式、错别字等均会影响报告的得分。\
（6）纸质版按本学期规定提交（过时不接受提交，未交纸质报告的实验成绩计零分，提交时间与地点另行通知），提交时每人均必须到场且签到，否则将拒收，不接受代交！实验未验收者拒收纸质报告！电子版未达要求者或未提交者拒收纸质版！\
（7）明显使用AI工具而未加思考的报告将视程度严格扣分！若发现报告是复制的，则不论复制者还是被复制者，他们的实验课成绩均计零分！

#pagebreak()

#show heading.where(level: 1): set text(font: "SimHei", size: 18pt, weight: "bold")
#show heading.where(level: 2): set text(font: "SimHei", size: 16pt, weight: "bold")
#show heading.where(level: 3): set text(font: "SimHei", size: 14pt, weight: "bold")
#set enum(numbering: "(1)")

#align(center)[
  #text(font: "SimHei", size: 15pt, weight: "bold")[#report-title]
]
