from django.urls import reverse_lazy
from django.templatetags.static import static

UNFOLD_SETTINGD = {
    "SITE_TITLE": "COSMORELAX",
    "SITE_HEADER": "COSMORELAX",
    "SITE_SUBHEADER": "Управленческий дашборд",
    # "SITE_ICON": lambda request: static("img/logo.svg"),
    "DASHBOARD_CALLBACK": "config.dashboard.dashboard_callback",

    # "SITE_SYMBOL": lambda request: static("img/logo.svg"),
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SHOW_BACK_BUTTON": True,
    "BORDER_RADIUS": "10px",
    "COLORS": {
        # Палитра «Поклонка Плэйс» (см. reportstyleguide) — Navy как primary
        # вместо стандартного фиолетового Unfold. base/font оставлены
        # дефолтными (уже нейтральные и проверены в тёмной теме).
        "primary": {
            "50":  "#F4F8FA",  # Pale Soft
            "100": "#EAF1F5",  # Pale
            "200": "#D8E1E7",  # Line
            "300": "#ADC3D0",
            "400": "#6D97AC",  # Sky
            "500": "#2F6B8A",  # Blue
            "600": "#18324A",  # Navy — основной
            "700": "#13293D",
            "800": "#102436",  # Navy Dark
            "900": "#0B1A28",
            "950": "#07111B",
        },
    },
    "STYLES": [
        lambda request: static("css/output.css"),
    ],
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Система",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Группы",
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                    {
                        "title": "Пользователи",
                        "icon": "person_3",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                    },
                    {
                        "title": "Команды",
                        "icon": "wand_shine",
                        "link": reverse_lazy("admin:core_jobs_changelist"),
                    },
                ],
            },
            # Главная книга
            {
                "title": "Главная книга",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "План счетов",
                        "icon": "account_tree",
                        "link": reverse_lazy("admin:gl_glaccount_changelist"),
                    },
                    {
                        "title": "Журнал проводок",
                        "icon": "menu_book",
                        "link": reverse_lazy("admin:gl_journalentry_changelist"),
                    },
                ],
            },
            # Дашборды
            {
                "title": "Дашборды",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Отчёт ДДС",
                        "icon": "table_chart",
                        "link": reverse_lazy("admin:dashboard_cashflow_changelist"),
                    },
                    {
                        "title": "Остатки по дням",
                        "icon": "account_balance_wallet",
                        "link": reverse_lazy("admin:dashboard_cashbalanceday_changelist"),
                    },
                    {
                        "title": "Сверка с банком",
                        "icon": "fact_check",
                        "link": reverse_lazy("admin:dashboard_cashcheck_changelist"),
                    },
                    {
                        "title": "Контрагенты и статьи",
                        "icon": "manage_search",
                        "link": reverse_lazy("admin:dashboard_cpaudit_changelist"),
                    },
                ],
            },
            # Справочники
            {
                "title": "Справочники",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Контрагенты",
                        "icon": "groups",
                        "link": reverse_lazy("admin:cp_cp_changelist"),
                    },
                    {
                        "title": "Макро",
                        "icon": "candlestick_chart",
                        "link": reverse_lazy("admin:macro_fx_changelist"),
                    },
                ],
            },
            # Казначейство
            {
                "title": "Казначейство",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Банковские выписки",
                        "icon": "receipt",
                        "link": reverse_lazy("admin:treasury_statement_changelist"),
                    },
                    {
                        "title": "Операции по выпискам",
                        "icon": "list_alt",
                        "link": reverse_lazy("admin:treasury_bsline_changelist"),
                    },
                    {
                        "title": "Банковские счета",
                        "icon": "account_balance_wallet",
                        "link": reverse_lazy("admin:treasury_bankaccount_changelist"),
                    },
                    {
                        "title": "Статьи ДДС",
                        "icon": "account_tree",
                        "link": reverse_lazy("admin:treasury_cfitem_tree"),
                    },
                    {
                        "title": "Резолверы контрагентов",
                        "icon": "person_pin",
                        "link": reverse_lazy("admin:treasury_cpresolver_changelist"),
                    },
                    {
                        "title": "Резолверы КБК",
                        "icon": "rule",
                        "link": reverse_lazy("admin:treasury_kbkresolver_changelist"),
                    },
                    {
                        "title": "Резолверы счетов",
                        "icon": "pin",
                        "link": reverse_lazy("admin:treasury_baresolver_changelist"),
                    },
                    {
                        "title": "Резолверы: между своими",
                        "icon": "sync_alt",
                        "link": reverse_lazy("admin:treasury_icresolver_changelist"),
                    },
                ],
            },
            
                      
        ],
    },
    "TABS": [
        {
            "models": ["cp.cp", "cp.gr", "cp.cpgroup"],
            "items": [
                {
                    "title": "Контрагенты",
                    "link": reverse_lazy("admin:cp_cp_changelist"),
                },
                {
                    "title": "Компании группы",
                    "link": reverse_lazy("admin:cp_gr_changelist"),
                },
                {
                    "title": "Группы контрагентов",
                    "link": reverse_lazy("admin:cp_cpgroup_changelist"),
                },
            ],
        },
        {
            "models": [
                "treasury.statement",
                "treasury.bankaccount",
                "treasury.cfitem",
                "treasury.bsline",
                "treasury.cpresolver",
                "treasury.kbkresolver",
                "treasury.baresolver",
                "treasury.icresolver",
            ],
            "items": [
                {
                    "title": "Банковские выписки",
                    "link": reverse_lazy("admin:treasury_statement_changelist"),
                },
                {
                    "title": "Операции по выпискам",
                    "link": reverse_lazy("admin:treasury_bsline_changelist"),
                },
                {
                    "title": "Банковские счета",
                    "link": reverse_lazy("admin:treasury_bankaccount_changelist"),
                },
                {
                    "title": "Статьи ДДС",
                    "link": reverse_lazy("admin:treasury_cfitem_tree"),
                },
                {
                    "title": "Резолверы контрагентов",
                    "link": reverse_lazy("admin:treasury_cpresolver_changelist"),
                },
                {
                    "title": "Резолверы КБК",
                    "link": reverse_lazy("admin:treasury_kbkresolver_changelist"),
                },
                {
                    "title": "Резолверы счетов",
                    "link": reverse_lazy("admin:treasury_baresolver_changelist"),
                },
                {
                    "title": "Между своими",
                    "link": reverse_lazy("admin:treasury_icresolver_changelist"),
                },
            ],
        },
    ],
    
    
}