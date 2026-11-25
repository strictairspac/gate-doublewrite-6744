FIXTURES = [
    {
        "code": "GT-N1-1124",
        "title": "北一闸掉电页巡检",
        "description": "一次断电后的五个页样本，核查撕裂页、双损页、未落双写区及过期副本。",
        "payload": {
            "pages": [
                {"page": "N1-021", "generation": 8, "event": "torn",
                 "before": "GATE21 07:10 压力 18kPa", "after": "GATE21 07:18 试启通过 22kPa"},
                {"page": "N1-022", "generation": 4, "event": "complete",
                 "before": "GATE22 07:12 开度 4cm", "after": "GATE22 07:20 开度 11cm"},
                {"page": "N1-023", "generation": 6, "event": "both",
                 "before": "GATE23 07:13 待复位", "after": "GATE23 07:21 限位通过"},
                {"page": "N1-024", "generation": 12, "event": "before_shadow",
                 "before": "GATE24 07:14 传感器待检", "after": "GATE24 07:22 传感器复位"},
                {"page": "N1-025", "generation": 3, "event": "stale",
                 "before": "GATE25 07:15 等待复核", "after": "GATE25 07:23 复核已签"},
            ]
        },
    },
    {
        "code": "GT-S2-1124",
        "title": "南二闸同代冲突",
        "description": "数据页和双写页各自校验通过，但同一代包含两条不同的复位说明。",
        "payload": {
            "pages": [
                {"page": "S2-006", "generation": 11, "event": "disagree",
                 "before": "GATE6 08:05 下落正常", "after": "GATE6 08:11 复位正常"},
                {"page": "S2-007", "generation": 5, "event": "before_target",
                 "before": "GATE7 08:07 已关闭", "after": "GATE7 08:15 开度 6cm"},
            ],
        },
    },
]
