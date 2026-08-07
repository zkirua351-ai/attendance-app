import streamlit as st
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials

page = st.sidebar.selectbox(
    "画面を選択してください",
    ["打刻画面", "管理者画面"]
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"], scopes=SCOPES
)
client = gspread.authorize(creds)
sheet = client.open_by_key(
    st.secrets["app"]["spreadsheet_key"]
).worksheet("punches")

def load_employees():
    staff_sheet = client.open_by_key(
        st.secrets["app"]["spreadsheet_key"]
    ).worksheet("staff")
    records = staff_sheet.get_all_records()
    return {str(r["社員コード"]): r["氏名"] for r in records}

employees = load_employees()

def attendance(employee_code, action):
    if employee_code == "":
        st.error("社員コードを入力してください")
    elif employee_code not in employees:
        st.error("登録されていない社員コードです")
    else:
        employee_name = employees[employee_code]
        records = sheet.get_all_records()

        if records:
            attendance_data = pd.DataFrame(records)
            employee_records = attendance_data[
                attendance_data["社員コード"] == employee_code
            ]
            if not employee_records.empty:
                last_action = employee_records.iloc[-1]["区分"]
                if last_action == action:
                    st.error(f"すでに{action}済みです")
                    return

        now = datetime.now(ZoneInfo("Asia/Tokyo"))
        sheet.append_row([
            now.strftime("%Y%m%d-%H%M%S") + "-" + employee_code,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d"),
            employee_code,
            employee_name,
            action,
            "本人",
            "有効",
            "",
            "",
        ])
        st.success(f"{employee_name}さんの{action}を受け付けました")


def get_state(employee_code):
    records = sheet.get_all_records()
    if not records:
        return "未出勤"

    attendance_data = pd.DataFrame(records)
    employee_records = attendance_data[
        attendance_data["社員コード"] == employee_code
    ]
    if employee_records.empty:
        return "未出勤"

    last_action = employee_records.iloc[-1]["区分"]
    if last_action == "退勤":
        return "未出勤"
    elif last_action == "出勤":
        return "勤務中"
    elif last_action == "休憩開始":
        return "休憩中"
    elif last_action == "休憩終了":
        return "勤務中"


def calc_work_minutes(day_records):
    day_records = day_records[day_records["状態"] == "有効"]
    start_rows = day_records[day_records["区分"] == "出勤"]
    end_rows = day_records[day_records["区分"] == "退勤"]

    if start_rows.empty:
        return None, "出勤の打刻がありません", "", ""
    if end_rows.empty:
        return None, "退勤の打刻がありません", "", ""
    if len(start_rows) > 1 or len(end_rows) > 1:
        return None, "出勤または退勤が複数回あります", "", ""

    start = datetime.strptime(start_rows.iloc[0]["打刻日時"], "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(end_rows.iloc[0]["打刻日時"], "%Y-%m-%d %H:%M:%S")
    total = int((end - start).total_seconds() // 60)

    start_time_str = start.strftime("%H:%M")
    end_time_str = end.strftime("%H:%M")

    break_starts = day_records[day_records["区分"] == "休憩開始"]
    break_ends = day_records[day_records["区分"] == "休憩終了"]
    break_minutes = 0

    for i in range(len(break_starts)):
        if i >= len(break_ends):
            return None, "休憩終了の打刻がありません", "", ""
        bs = datetime.strptime(break_starts.iloc[i]["打刻日時"], "%Y-%m-%d %H:%M:%S")
        be = datetime.strptime(break_ends.iloc[i]["打刻日時"], "%Y-%m-%d %H:%M:%S")
        break_minutes += int((be - bs).total_seconds() // 60)

    return total - break_minutes, "", start_time_str, end_time_str


def format_minutes(minutes):
    h = minutes // 60
    m = minutes % 60
    return f"{h}:{m:02d}"


def build_daily_summary(attendance_data):
    results = []

    for (work_date, emp_code), group in attendance_data.groupby(["勤務日", "社員コード"]):
        minutes, reason, start_time, end_time = calc_work_minutes(group)

        if minutes is None:
            results.append({
                "勤務日": work_date,
                "社員コード": emp_code,
                "氏名": employees[emp_code],
                "出勤":  "―",      
                "退勤":  "―",      
                "実働(分)": "―",
                "通常(分)": "―",
                "残業(分)": "―",
                "通常(時:分)": "―",
                "残業(時:分)": "―",
                "状態": f"⚠️ {reason}",  
            })
        else:
            normal = min(minutes, 480)
            overtime = max(minutes - 480, 0)
            results.append({
                "勤務日": work_date,
                "社員コード": emp_code,
                "氏名": employees[emp_code],
                "出勤": start_time,        
                "退勤": end_time,        
                "通常(分)": normal,
                "残業(分)": overtime,
                "実働(分)": minutes,
                "通常(時:分)": format_minutes(normal),
                "残業(時:分)": format_minutes(overtime),
                "状態": "",
            })
    return results


def build_monthly_summary(results):
    monthly = []
    df_daily = pd.DataFrame(results)

    for emp_code, group in df_daily.groupby("社員コード"):
        incomplete = len(group[group["状態"] != ""])

        if incomplete > 0:
            monthly.append({
                "社員コード": emp_code,
                "氏名": employees[emp_code],
                "出勤日数": "―",
                "通常(分)": "―",
                "残業(分)": "―",
                "通常(時:分)": "―",
                "残業(時:分)": "―",
                "状態": f"⚠️ 未確定 {incomplete}日。修正が必要です",
            })
        else:
            normal_sum = group["通常(分)"].sum()
            overtime_sum = group["残業(分)"].sum()
            monthly.append({
                "社員コード": emp_code,
                "氏名": employees[emp_code],
                "出勤日数": len(group),
                "通常(分)": normal_sum,
                "残業(分)": overtime_sum,
                "通常(時:分)": format_minutes(normal_sum),
                "残業(時:分)": format_minutes(overtime_sum),
                "状態": "",
            })

    return monthly


if page == "打刻画面":
    st.title("勤怠管理アプリ")

    employee_code = st.text_input("社員コードを入力してください")

    if employee_code == "":
        pass
    elif employee_code not in employees:
        st.error("登録されていない社員コードです")
    else:
        st.write(f"### {employees[employee_code]} さん")
        state = get_state(employee_code)
        st.info(f"現在の状態：{state}")

        if state == "未出勤":
            if st.button("出勤"):
                attendance(employee_code, "出勤")
        elif state == "勤務中":
            if st.button("休憩開始"):
                attendance(employee_code, "休憩開始")
            if st.button("退勤"):
                attendance(employee_code, "退勤")
        elif state == "休憩中":
            if st.button("休憩終了"):
                attendance(employee_code, "休憩終了")


elif page == "管理者画面":
    st.title("管理者画面")

    admin_password = st.text_input(
        "管理者パスワードを入力してください",
        type="password"
    )
    correct_admin_password = st.secrets["app"]["admin_password"]

    if admin_password == "":
        pass
    elif admin_password != correct_admin_password:
        st.error("パスワードが違います")
    else:
        records = sheet.get_all_records()

        if not records:
            st.info("打刻記録はまだありません")
        else:
            attendance_data = pd.DataFrame(records)

            admin_tab = st.selectbox(
                "表示する内容",
                ["未完了の確認", "集計", "打刻の修正", "打刻記録一覧"]
            )

            # ===== 未完了の確認 =====
            if admin_tab == "未完了の確認":
                st.subheader("未完了の打刻")

                results = build_daily_summary(attendance_data)
                incomplete_rows = [r for r in results if r["状態"] != ""]

                if incomplete_rows:
                    st.dataframe(
                        pd.DataFrame(incomplete_rows)[["勤務日", "氏名", "状態"]],
                        use_container_width=True
                    )
                else:
                    st.success("すべての打刻が完了しています")

            # ===== 集計 =====
            elif admin_tab == "集計":
                results = build_daily_summary(attendance_data)

                st.subheader("日別集計")
                st.dataframe(pd.DataFrame(results), use_container_width=True)

                st.subheader("月次集計")
                monthly = build_monthly_summary(results)
                st.dataframe(pd.DataFrame(monthly), use_container_width=True)

                df_monthly = pd.DataFrame(monthly)
                csv = df_monthly.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    label="月次集計をCSVでダウンロード",
                    data=csv,
                    file_name="月次集計.csv",
                    mime="text/csv",
                )

            # ===== 打刻の修正 =====
            elif admin_tab == "打刻の修正":
                st.subheader("打刻の修正")

                selected_emp = st.selectbox(
                    "社員を選択",
                    list(employees.keys()),
                    format_func=lambda x: employees[x]
                )

                emp_punches = attendance_data[
                    attendance_data["社員コード"] == selected_emp
                ]

                if emp_punches.empty:
                    st.info("この社員の打刻記録はありません")
                else:
                    work_dates = emp_punches["勤務日"].unique()
                    selected_date = st.selectbox("勤務日を選択", work_dates)

                    day_punches = emp_punches[
                        emp_punches["勤務日"] == selected_date
                    ]

                    options = {}
                    for _, row in day_punches.iterrows():
                        label = f"{row['区分']}  {row['打刻日時']}  [{row['状態']}]"
                        options[label] = row["id"]

                    selected_label = st.selectbox("打刻を選択", list(options.keys()))
                    selected_id = options[selected_label]

                    if st.button("この打刻を取り消す"):
                        row_number = None
                        for i in range(len(records)):
                            if records[i]["id"] == selected_id:
                                row_number = i + 2
                                break
                        sheet.update_cell(row_number, 8, "取消")
                        st.success("打刻を取り消しました")
                        st.rerun()

                    st.markdown("---")
                    st.write("正しい打刻を追加")

                    new_action = st.selectbox(
                        "区分",
                        ["出勤", "休憩開始", "休憩終了", "退勤"]
                    )
                    new_date = st.date_input("日付")
                    new_time_only = st.time_input("時刻", step=60)
                    new_datetime = datetime.combine(new_date, new_time_only)
                    new_time = new_datetime.strftime("%Y-%m-%d %H:%M:%S")
                    new_reason = st.text_input("理由（例: 退勤打刻忘れ）")

                    if st.button("この打刻を追加"):
                        sheet.append_row([
                            new_time.replace(" ", "").replace(":", "").replace("-", "") + "-" + selected_emp,
                            new_time,
                            selected_date,
                            selected_emp,
                            employees[selected_emp],
                            new_action,
                            "店長修正",
                            "有効",
                            selected_id,
                            new_reason,
                        ])
                        st.success("打刻を追加しました")
                        st.rerun()

            # ===== 打刻記録一覧 =====
            elif admin_tab == "打刻記録一覧":
                st.subheader("打刻記録一覧")
                st.dataframe(attendance_data, use_container_width=True)