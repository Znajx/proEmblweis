# -*- coding: utf-8 -*-
"""نظام إدارة الحضور والانصراف - Python 3.14 / Tkinter / SQLite"""
import csv, hashlib, sqlite3, tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, date, timedelta
from pathlib import Path

BASE=Path(__file__).resolve().parent
DB=BASE/'attendance.db'
# هوية بصرية كلاسيكية: كحلي داكن، عاجي، وذهبي
C={'navy':'#17263D','navy2':'#223653','gold':'#B58A45','gold2':'#D8B978','cream':'#F4F0E7','paper':'#FFFEFB','ink':'#273449','muted':'#788294','line':'#DED7C9','green':'#47745B','red':'#A84E4E','orange':'#A97735','white':'#FFFFFF'}
DATE_FMT='%Y-%m-%d'

import hmac, secrets, shutil, os

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310000)
    return "pbkdf2_sha256$310000$" + salt.hex() + "$" + digest.hex()

def verify_password(password, stored):
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, digest = stored.split("$")
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)).hex()
            return hmac.compare_digest(actual, digest)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(sha(password), stored)

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class DatabaseConnection(sqlite3.Connection):
    """اتصال SQLite يحفظ التغييرات ثم يغلق نفسه عند الخروج من with."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    c = sqlite3.connect(DB, factory=DatabaseConnection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c
def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,full_name TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'مدير',active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,description TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS shifts(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,start_time TEXT NOT NULL,end_time TEXT NOT NULL,grace_minutes INTEGER NOT NULL DEFAULT 10);
        CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY,emp_code TEXT UNIQUE NOT NULL,full_name TEXT NOT NULL,phone TEXT DEFAULT '',department_id INTEGER,shift_id INTEGER,hire_date TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'نشط',FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL,FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS leave_requests(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,leave_type TEXT NOT NULL,reason TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'قيد المراجعة',FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE RESTRICT);
        CREATE TABLE IF NOT EXISTS workdays(weekday INTEGER PRIMARY KEY, is_workday INTEGER NOT NULL CHECK(is_workday IN (0,1)));
        CREATE TABLE IF NOT EXISTS holidays(id INTEGER PRIMARY KEY,holiday_date TEXT UNIQUE NOT NULL,name TEXT NOT NULL,notes TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY,username TEXT NOT NULL,action TEXT NOT NULL,details TEXT DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        # ترقية قاعدة البيانات القديمة دون المساس بالموظفين والأقسام والإجازات.
        cols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
        if 'role' not in cols: c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'مدير'")
        if 'active' not in cols: c.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        c.execute("""CREATE TABLE IF NOT EXISTS attendance_new(
            id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, work_date TEXT NOT NULL,
            check_in TEXT, check_out TEXT, notes TEXT DEFAULT '', shift_id INTEGER,
            FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE RESTRICT,
            FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE RESTRICT,
            UNIQUE(employee_id,work_date,shift_id))""")
        oldcols={r['name'] for r in c.execute('PRAGMA table_info(attendance)')}
        if oldcols:
            for r in c.execute('SELECT * FROM attendance').fetchall():
                emp=c.execute('SELECT shift_id FROM employees WHERE id=?',(r['employee_id'],)).fetchone()
                sid=emp['shift_id'] if emp else None
                c.execute("""INSERT OR IGNORE INTO attendance_new(id,employee_id,work_date,check_in,check_out,notes,shift_id) VALUES(?,?,?,?,?,?,?)""",
                    (r['id'],r['employee_id'],r['work_date'],r['check_in'],r['check_out'],r['notes'],sid))
            c.execute('DROP TABLE attendance')
        c.execute('ALTER TABLE attendance_new RENAME TO attendance')
        c.execute('CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(work_date)')
        c.executemany('INSERT OR IGNORE INTO workdays(weekday,is_workday) VALUES(?,?)',[(i,0 if i in (4,5) else 1) for i in range(7)])
        # الحساب الافتراضي ينشأ مرة واحدة فقط، ولا يعاد تعيين كلمة مروره.
        if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            c.execute('INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)',
                      ('admin',hash_password('Admin@123'),'مدير النظام','مدير'))
        if not c.execute('SELECT 1 FROM departments LIMIT 1').fetchone():
            c.executemany('INSERT INTO departments(name,description) VALUES(?,?)',[('الإدارة','الإدارة العامة'),('تقنية المعلومات','قسم التقنية'),('المحاسبة','الشؤون المالية')])
        if not c.execute('SELECT 1 FROM shifts LIMIT 1').fetchone():
            c.executemany('INSERT INTO shifts(name,start_time,end_time,grace_minutes) VALUES(?,?,?,?)',[('الدوام الصباحي','08:00','16:00',10),('الدوام المسائي','16:00','00:00',10)])

def parse_time12(s):
    s=s.strip().upper().replace('ص','AM').replace('م','PM').replace(' ','')
    for f in ('%I:%M%p','%I%p','%H:%M'):
        try:return datetime.strptime(s,f).strftime('%H:%M')
        except ValueError:pass
    raise ValueError('أدخل الوقت مثل 08:30 AM أو 04:15 PM')
def display12(stamp):
    if not stamp:return '—'
    try:return datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S').strftime('%I:%M %p')
    except ValueError:return stamp

def duration(a,b):
    x=datetime.strptime(a,'%Y-%m-%d %H:%M:%S');y=datetime.strptime(b,'%Y-%m-%d %H:%M:%S')
    if y<x:y+=timedelta(days=1)
    return max(0,int((y-x).total_seconds()))
def hours(seconds):return f'{seconds/3600:.2f}'
def valid_date(s):return datetime.strptime(s.strip(),DATE_FMT).date()

def is_workday(day, workdays, holidays):
    """يحدد يوم العمل: يجب أن يكون مفعلاً في الإعدادات وألا يكون عطلة رسمية."""
    return bool(workdays.get(day.weekday(), 1)) and day.isoformat() not in holidays


class App(tk.Tk):
 def __init__(self):
    super().__init__();self.title('نظام إدارة الحضور والانصراف');self.geometry('1360x850');self.minsize(1120,720);self.configure(bg=C['cream'])
    self.style=ttk.Style(self);self.style.theme_use('clam')
    self.style.configure('Treeview',font=('Arial',10),rowheight=32,background=C['paper'],fieldbackground=C['paper'],foreground=C['ink'],bordercolor=C['line'],relief='flat')
    self.style.configure('Treeview.Heading',font=('Arial',10,'bold'),background=C['navy'],foreground=C['gold2'],padding=9,relief='flat')
    self.style.map('Treeview',background=[('selected','#E9DFC9')],foreground=[('selected',C['navy'])])
    self.style.configure('TCombobox',padding=7,fieldbackground=C['paper'],background=C['paper'])
    self.style.configure('TScrollbar',background=C['cream'])
    self.login_screen()
 def clear(self):
    for w in self.winfo_children():w.destroy()
 def panel(self,p,title=None):
    f=tk.LabelFrame(p,text=title or '',bg=C['paper'],fg=C['gold'],font=('Arial',10,'bold'),padx=12,pady=12,labelanchor='ne',bd=1,relief='solid',highlightbackground=C['line']);return f
 def label(self,p,text,**kw):return tk.Label(p,text=text,bg=kw.pop('bg',C['paper']),fg=kw.pop('fg',C['ink']),font=kw.pop('font',('Arial',10)),**kw)
 def entry(self,p,width=20):return tk.Entry(p,width=width,justify='right',font=('Arial',11),bg=C['white'],fg=C['ink'],relief='solid',bd=1,highlightthickness=1,highlightbackground=C['line'],highlightcolor=C['gold'])
 def btn(self,p,text,fn,color=None):
    color=color or C['gold'];return tk.Button(p,text=text,command=fn,bg=color,fg=C['white'] if color!=C['gold2'] else C['navy'],activebackground=C['navy2'],activeforeground=C['white'],relief='flat',bd=0,font=('Arial',10,'bold'),padx=15,pady=9,cursor='hand2')
 def login_screen(self):
    self.clear();self.configure(bg=C['navy'])
    outer=tk.Frame(self,bg=C['navy']);outer.place(relx=.5,rely=.5,anchor='center')
    tk.Frame(outer,bg=C['gold'],height=5).pack(fill='x')
    box=tk.Frame(outer,bg=C['paper'],padx=45,pady=38,highlightbackground=C['gold'],highlightthickness=1);box.pack()
    self.label(box,'◆',fg=C['gold'],font=('Arial',34,'bold')).pack(pady=(0,8))
    self.label(box,'نظام إدارة الحضور والانصراف',font=('Arial',20,'bold'),fg=C['navy']).pack()
    self.label(box,'بوابة الدخول الآمن',fg=C['muted']).pack(pady=(8,24))
    self.label(box,'اسم المستخدم',font=('Arial',10,'bold')).pack(anchor='e')
    self.user=self.entry(box,32);self.user.pack(ipady=9,pady=(5,14))
    self.label(box,'كلمة المرور',font=('Arial',10,'bold')).pack(anchor='e')
    self.pw=self.entry(box,32);self.pw.configure(show='●');self.pw.pack(ipady=9,pady=(5,18));self.pw.bind('<Return>',lambda e:self.login())
    self.btn(box,'تسجيل الدخول',self.login,C['navy']).pack(fill='x',ipady=3)
    self.label(box,'نظام داخلي — بيانات الدخول لا تظهر في الشاشة الرئيسية',fg=C['muted'],font=('Arial',9)).pack(pady=(18,0))
    self.configure(bg=C['cream'])
 def login(self):
    with connect() as c:r=c.execute('SELECT * FROM users WHERE username=?',(self.user.get().strip(),)).fetchone()
    if not r or not r['active'] or not verify_password(self.pw.get(),r['password_hash']):messagebox.showerror('تعذر الدخول','تحقق من اسم المستخدم وكلمة المرور.');return
    if not r['password_hash'].startswith('pbkdf2_sha256$'):
     with connect() as c:c.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(self.pw.get()),r['id']))
    self.full_name=r['full_name'];self.user_id=r['id'];self.role=r['role'];self.username=r['username'];self.audit('تسجيل الدخول','');self.shell();self.dashboard()
 def shell(self):
    self.clear();self.configure(bg=C['cream'])
    head=tk.Frame(self,bg=C['navy'],height=76);head.pack(fill='x');head.pack_propagate(False)
    tk.Frame(head,bg=C['gold'],width=7).pack(side='right',fill='y')
    self.label(head,'نظام إدارة الحضور والانصراف',bg=C['navy'],fg=C['gold2'],font=('Arial',19,'bold')).pack(side='right',padx=22)
    self.label(head,date.today().strftime('%Y-%m-%d'),bg=C['navy'],fg='#E9E1D2',font=('Arial',10)).pack(side='left',padx=22)
    body=tk.Frame(self,bg=C['cream']);body.pack(fill='both',expand=True)
    nav=tk.Frame(body,bg=C['navy'],width=225);nav.pack(side='right',fill='y');nav.pack_propagate(False)
    self.label(nav,'القائمة الرئيسية',bg=C['navy'],fg=C['gold2'],font=('Arial',12,'bold')).pack(anchor='e',padx=18,pady=22)
    items=[('⌂  لوحة التحكم',self.dashboard),('♙  إدارة الموظفين',self.employees),('▦  إدارة الأقسام',self.departments),('◷  إدارة الورديات',self.shifts),('↔  تسجيل الحضور والانصراف',self.punch),('▤  سجل الحضور اليومي',self.daily),('✧  إدارة الإجازات',self.leaves),('▥  التقارير الشهرية',self.reports),('▧  أيام العمل والعطلات',self.calendar_settings),('♙  المستخدمون والصلاحيات',self.users_screen),('▤  سجل العمليات',self.audit_screen),('◈  النسخ الاحتياطي',self.backup_screen)]
    if self.role=='مسؤول':items=[item for item in items if item[0] not in ('▧  أيام العمل والعطلات','♙  المستخدمون والصلاحيات','▤  سجل العمليات','◈  النسخ الاحتياطي')]
    elif self.role=='مستخدم':items=[item for item in items if item[0] in ('⌂  لوحة التحكم','↔  تسجيل الحضور والانصراف','▤  سجل الحضور اليومي')]
    for text,fn in items:
     b=tk.Button(nav,text=text,command=fn,anchor='e',bg=C['navy'],fg='#F6F0E5',activebackground=C['navy2'],activeforeground=C['gold2'],relief='flat',font=('Arial',10,'bold'),pady=13,padx=15,cursor='hand2');b.pack(fill='x',padx=7,pady=2)
    tk.Frame(nav,bg=C['gold'],height=1).pack(fill='x',padx=12,pady=14)
    tk.Button(nav,text='تسجيل الخروج',command=self.login_screen,anchor='center',bg='#35435A',fg='#F2D9D9',activebackground=C['red'],relief='flat',font=('Arial',10,'bold'),pady=11).pack(side='bottom',fill='x',padx=10,pady=15)
    self.area=tk.Frame(body,bg=C['cream']);self.area.pack(side='left',fill='both',expand=True,padx=22,pady=20)
 def page(self,title,sub=''):
    for w in self.area.winfo_children():w.destroy()
    self.label(self.area,title,bg=C['cream'],fg=C['navy'],font=('Arial',21,'bold')).pack(anchor='e')
    tk.Frame(self.area,bg=C['gold'],height=3,width=100).pack(anchor='e',pady=(7,5))
    if sub:self.label(self.area,sub,bg=C['cream'],fg=C['muted'],font=('Arial',10)).pack(anchor='e',pady=(0,12))
 def tree(self,parent,cols,heads,widths=None):
    wrap=tk.Frame(parent,bg=C['paper'],highlightbackground=C['line'],highlightthickness=1);wrap.pack(fill='both',expand=True,pady=9)
    t=ttk.Treeview(wrap,columns=cols,show='headings')
    for i,k in enumerate(cols):t.heading(k,text=heads[i]);t.column(k,width=widths[i] if widths else 120,anchor='center',stretch=True)
    sy=ttk.Scrollbar(wrap,orient='vertical',command=t.yview);sx=ttk.Scrollbar(wrap,orient='horizontal',command=t.xview);t.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
    t.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew');wrap.rowconfigure(0,weight=1);wrap.columnconfigure(0,weight=1);return t
 def audit(self,action,details=''):
    try:
     with connect() as c:c.execute('INSERT INTO audit_log(username,action,details,created_at) VALUES(?,?,?,?)',(getattr(self,'username','system'),action,details,datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    except sqlite3.Error: pass
 def calendar_settings(self):
    self.page('أيام العمل والعطلات','حدد أيام العمل الأسبوعية وأضف العطلات الرسمية التي تستثنى من حساب الغياب.')
    box=self.panel(self.area,'أيام الأسبوع');box.pack(fill='x')
    names=['الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد'];vars=[]
    with connect() as c: data={r['weekday']:r['is_workday'] for r in c.execute('SELECT * FROM workdays')}
    for i,n in enumerate(names):
     v=tk.IntVar(value=data.get(i,1));vars.append(v);tk.Checkbutton(box,text=n,variable=v,bg=C['paper'],font=('Arial',10)).pack(side='right',padx=8)
    def save_days():
     with connect() as c:
      for i,v in enumerate(vars):c.execute('INSERT INTO workdays(weekday,is_workday) VALUES(?,?) ON CONFLICT(weekday) DO UPDATE SET is_workday=excluded.is_workday',(i,v.get()))
     self.audit('تعديل أيام العمل');messagebox.showinfo('تم','تم حفظ أيام العمل.')
    self.btn(box,'حفظ أيام العمل',save_days,C['green']).pack(side='left',padx=5)
    f=self.panel(self.area,'العطلات الرسمية');f.pack(fill='x',pady=10);d=self.entry(f,16);d.insert(0,date.today().isoformat());n=self.entry(f,30)
    self.label(f,'التاريخ YYYY-MM-DD',font=('Arial',9,'bold')).grid(row=0,column=3);d.grid(row=1,column=3,padx=6,pady=6);self.label(f,'اسم المناسبة',font=('Arial',9,'bold')).grid(row=0,column=2);n.grid(row=1,column=2,padx=6,pady=6)
    t=self.tree(self.area,('id','day','name'),('ID','التاريخ','المناسبة'))
    def refresh():
     t.delete(*t.get_children())
     with connect() as c:rows=c.execute('SELECT * FROM holidays ORDER BY holiday_date').fetchall()
     for r in rows:t.insert('','end',iid=str(r['id']),values=(r['id'],r['holiday_date'],r['name']))
    def add():
     try:
      valid_date(d.get()); 
      with connect() as c:c.execute('INSERT INTO holidays(holiday_date,name) VALUES(?,?)',(d.get().strip(),n.get().strip()))
      self.audit('إضافة عطلة',n.get().strip());refresh()
     except (ValueError,sqlite3.IntegrityError) as e:messagebox.showerror('تعذر الحفظ','تحقق من التاريخ والاسم وعدم تكرار التاريخ.')
    def delete():
     if not t.selection():return
     with connect() as c:c.execute('DELETE FROM holidays WHERE id=?',(t.selection()[0],))
     self.audit('حذف عطلة',t.selection()[0]);refresh()
    row=tk.Frame(self.area,bg=C['cream']);row.pack(fill='x');self.btn(row,'إضافة عطلة',add,C['green']).pack(side='right',padx=4);self.btn(row,'حذف المحدد',delete,C['red']).pack(side='right',padx=4);refresh()
 def users_screen(self):
    if self.role!='مدير':
     messagebox.showerror('صلاحيات غير كافية','هذه الشاشة متاحة للمدير فقط.');return
    self.page('المستخدمون والصلاحيات','إدارة حسابات الدخول وتحديد الدور وتفعيل الحساب أو تعطيله.')
    f=self.panel(self.area,'بيانات المستخدم');f.pack(fill='x');fs={}
    specs=[('username','اسم المستخدم'),('full_name','الاسم الكامل'),('password','كلمة المرور')]
    for i,(k,label) in enumerate(specs):
     box=tk.Frame(f,bg=C['paper']);box.grid(row=0,column=2-i,padx=8,sticky='ew');self.label(box,label,font=('Arial',9,'bold')).pack(anchor='e');w=self.entry(box,24)
     if k=='password':w.configure(show='●')
     w.pack(fill='x',ipady=6,pady=4);fs[k]=w;f.columnconfigure(i,weight=1)
    rolebox=tk.Frame(f,bg=C['paper']);rolebox.grid(row=0,column=3,padx=8,sticky='ew');self.label(rolebox,'الدور',font=('Arial',9,'bold')).pack(anchor='e');role=ttk.Combobox(rolebox,values=['مدير','مسؤول','مستخدم'],state='readonly',justify='right');role.set('مستخدم');role.pack(fill='x',ipady=5,pady=4)
    active=tk.IntVar(value=1);tk.Checkbutton(f,text='الحساب نشط',variable=active,bg=C['paper']).grid(row=1,column=3,sticky='e',padx=8)
    t=self.tree(self.area,('id','username','full_name','role','active'),('ID','اسم المستخدم','الاسم الكامل','الدور','الحالة'));sel={'id':None}
    def refresh():
     t.delete(*t.get_children())
     with connect() as c:rs=c.execute('SELECT id,username,full_name,role,active FROM users ORDER BY id').fetchall()
     for r in rs:t.insert('','end',iid=str(r['id']),values=(r['id'],r['username'],r['full_name'],r['role'],'نشط' if r['active'] else 'معطل'))
    def clear():
     sel['id']=None
     for w in fs.values():w.delete(0,'end')
     role.set('مستخدم');active.set(1);t.selection_remove(t.selection())
    def choose(_):
     if not t.selection():return
     v=t.item(t.selection()[0],'values');sel['id']=int(v[0])
     for k,x in zip(('username','full_name'),v[1:3]):fs[k].delete(0,'end');fs[k].insert(0,x)
     fs['password'].delete(0,'end');role.set(v[3]);active.set(1 if v[4]=='نشط' else 0)
    def save():
     username=fs['username'].get().strip();full=fs['full_name'].get().strip();password=fs['password'].get()
     if not username or not full:messagebox.showwarning('بيانات ناقصة','اسم المستخدم والاسم الكامل مطلوبان.');return
     try:
      with connect() as c:
       if sel['id']:
        if password:c.execute('UPDATE users SET username=?,full_name=?,role=?,active=?,password_hash=? WHERE id=?',(username,full,role.get(),active.get(),hash_password(password),sel['id']))
        else:c.execute('UPDATE users SET username=?,full_name=?,role=?,active=? WHERE id=?',(username,full,role.get(),active.get(),sel['id']))
       else:
        if len(password)<8:raise ValueError('كلمة المرور الجديدة يجب أن تكون 8 أحرف على الأقل.')
        c.execute('INSERT INTO users(username,password_hash,full_name,role,active) VALUES(?,?,?,?,?)',(username,hash_password(password),full,role.get(),active.get()))
      self.audit('حفظ مستخدم',username);clear();refresh()
     except sqlite3.IntegrityError:messagebox.showerror('اسم مستخدم مكرر','اختر اسم مستخدم غير مستخدم.')
     except ValueError as ex:messagebox.showerror('بيانات غير صحيحة',str(ex))
    def delete():
     if not sel['id']:messagebox.showwarning('تنبيه','حدد مستخدمًا.');return
     if sel['id']==self.user_id:messagebox.showwarning('ممنوع','لا يمكنك تعطيل أو حذف حسابك الحالي من هذه الشاشة.');return
     if messagebox.askyesno('تأكيد','هل تريد تعطيل الحساب المحدد؟'):
      with connect() as c:c.execute('UPDATE users SET active=0 WHERE id=?',(sel['id'],))
      self.audit('تعطيل مستخدم',str(sel['id']));clear();refresh()
    bar=tk.Frame(self.area,bg=C['cream']);bar.pack(fill='x');self.btn(bar,'حفظ / تحديث',save,C['green']).pack(side='right',padx=4);self.btn(bar,'جديد',clear,C['navy2']).pack(side='right',padx=4);self.btn(bar,'تعطيل المستخدم',delete,C['red']).pack(side='right',padx=4);t.bind('<<TreeviewSelect>>',choose);refresh()
 def audit_screen(self):
    self.page('سجل العمليات','سجل محلي لأهم أحداث النظام.')
    t=self.tree(self.area,('user','action','details','created'),('المستخدم','العملية','التفاصيل','التاريخ والوقت'))
    with connect() as c:rows=c.execute('SELECT username,action,details,created_at FROM audit_log ORDER BY id DESC LIMIT 1000').fetchall()
    for r in rows:t.insert('','end',values=tuple(r))
 def backup_screen(self):
    self.page('النسخ الاحتياطي','أنشئ نسخة احتياطية أو استعد نسخة SQLite سليمة.')
    def backup():
     path=filedialog.asksaveasfilename(defaultextension='.db',filetypes=[('SQLite','*.db')],initialfile='attendance_backup.db')
     if not path:return
     try:
      with connect() as c:
       dest=sqlite3.connect(path);c.backup(dest);dest.close()
      self.audit('نسخ احتياطي',path);messagebox.showinfo('نجاح','تم إنشاء النسخة الاحتياطية.')
     except sqlite3.Error as e:messagebox.showerror('خطأ',str(e))
    def restore():
     path=filedialog.askopenfilename(filetypes=[('SQLite','*.db'),('All files','*.*')])
     if not path:return
     if not messagebox.askyesno('تأكيد الاستعادة','سيتم استبدال قاعدة البيانات الحالية. هل أنشأت نسخة احتياطية وتوافق؟'):return
     try:
      test=sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True);ok=test.execute('PRAGMA integrity_check').fetchone()[0]=='ok';test.close()
      if not ok:raise ValueError('فشل فحص سلامة قاعدة البيانات.')
      shutil.copy2(path,DB);self.audit('استعادة نسخة',path);messagebox.showinfo('نجاح','تمت الاستعادة. أعد تشغيل البرنامج.')
     except Exception as e:messagebox.showerror('تعذر الاستعادة',str(e))
    self.btn(self.area,'إنشاء نسخة احتياطية',backup,C['green']).pack(anchor='e',pady=12);self.btn(self.area,'استعادة نسخة احتياطية',restore,C['red']).pack(anchor='e')

 def departments(self):
    self.page('إدارة الأقسام','أضف أي عدد من الأقسام دون حد ثابت. رقم العرض تسلسلي ويتجدد بعد الحذف.')
    form=self.panel(self.area,'بيانات القسم');form.pack(fill='x');sel={'id':None};fields=[]
    for col,(txt,width) in enumerate([('اسم القسم',35),('الوصف',50)]):
     box=tk.Frame(form,bg=C['paper']);box.grid(row=0,column=1-col,padx=8,sticky='ew');self.label(box,txt,font=('Arial',10,'bold')).pack(anchor='e');w=self.entry(box,width);w.pack(fill='x',ipady=7,pady=5);fields.append(w);form.columnconfigure(col,weight=1)
    name,desc=fields
    t=self.tree(self.area,('seq','name','desc','count'),('م','اسم القسم','الوصف','عدد الموظفين'),(65,230,400,130))
    def refresh():
     t.delete(*t.get_children());
     with connect() as c:rs=c.execute('SELECT d.id,d.name,d.description,COUNT(e.id) n FROM departments d LEFT JOIN employees e ON e.department_id=d.id GROUP BY d.id ORDER BY d.name COLLATE NOCASE').fetchall()
     for i,r in enumerate(rs,1):t.insert('','end',iid=str(r['id']),values=(i,r['name'],r['description'],r['n']))
    def clear():sel['id']=None;name.delete(0,'end');desc.delete(0,'end');t.selection_remove(t.selection())
    def save():
     n=name.get().strip()
     if not n:messagebox.showwarning('بيانات ناقصة','اكتب اسم القسم.');return
     try:
      with connect() as c:
       if sel['id']:c.execute('UPDATE departments SET name=?,description=? WHERE id=?',(n,desc.get().strip(),sel['id']))
       else:c.execute('INSERT INTO departments(name,description) VALUES(?,?)',(n,desc.get().strip()))
      self.audit('حفظ قسم',n);clear();refresh()
     except sqlite3.IntegrityError:messagebox.showerror('اسم مكرر','يوجد قسم بهذا الاسم بالفعل.')
    def choose(_):
     if not t.selection():return
     sel['id']=int(t.selection()[0]);v=t.item(t.selection()[0],'values');name.delete(0,'end');name.insert(0,v[1]);desc.delete(0,'end');desc.insert(0,v[2])
    def delete():
     if not sel['id']:messagebox.showwarning('تنبيه','حدد قسمًا أولاً.');return
     if messagebox.askyesno('تأكيد الحذف','سيتم فصل الموظفين عن القسم دون حذفهم. هل تريد المتابعة؟'):
      with connect() as c:c.execute('DELETE FROM departments WHERE id=?',(sel['id'],))
      clear();refresh()
    row=tk.Frame(self.area,bg=C['cream']);row.pack(fill='x');self.btn(row,'حفظ القسم',save,C['green']).pack(side='right',padx=4);self.btn(row,'جديد',clear,C['navy2']).pack(side='right',padx=4);self.btn(row,'حذف',delete,C['red']).pack(side='right',padx=4)
    t.bind('<<TreeviewSelect>>',choose);refresh()
 def shifts(self):
    self.page('إدارة الورديات','اكتب أوقات الورديات بصيغة 12 ساعة مثل 08:00 AM أو 04:30 PM؛ تخزن داخليًا بصيغة 24 ساعة.')
    f=self.panel(self.area,'بيانات الوردية');f.pack(fill='x');fs={};spec=[('name','اسم الوردية',''),('start','بداية الدوام','08:00 AM'),('end','نهاية الدوام','04:00 PM'),('grace','سماح التأخير بالدقائق','10')]
    for i,(k,l,v) in enumerate(spec):
     b=tk.Frame(f,bg=C['paper']);b.grid(row=0,column=3-i,padx=7,sticky='ew');self.label(b,l,font=('Arial',9,'bold')).pack(anchor='e');w=self.entry(b,18);w.insert(0,v);w.pack(fill='x',ipady=6,pady=4);fs[k]=w;f.columnconfigure(i,weight=1)
    t=self.tree(self.area,('id','name','start','end','grace'),('ID','اسم الوردية','البداية','النهاية','السماح بالدقائق'));sel={'id':None}
    def refresh():
     t.delete(*t.get_children());
     with connect() as c:rs=c.execute('SELECT * FROM shifts ORDER BY name').fetchall()
     for r in rs:t.insert('','end',values=(r['id'],r['name'],datetime.strptime(r['start_time'],'%H:%M').strftime('%I:%M %p'),datetime.strptime(r['end_time'],'%H:%M').strftime('%I:%M %p'),r['grace_minutes']))
    def clear():
     sel['id']=None
     for k,w in fs.items():w.delete(0,'end')
     fs['start'].insert(0,'08:00 AM');fs['end'].insert(0,'04:00 PM');fs['grace'].insert(0,'10');t.selection_remove(t.selection())
    def save():
     try:
      a=parse_time12(fs['start'].get());b=parse_time12(fs['end'].get());g=int(fs['grace'].get());n=fs['name'].get().strip()
      if not n or g<0:raise ValueError
      with connect() as c:
       if sel['id']:c.execute('UPDATE shifts SET name=?,start_time=?,end_time=?,grace_minutes=? WHERE id=?',(n,a,b,g,sel['id']))
       else:c.execute('INSERT INTO shifts(name,start_time,end_time,grace_minutes) VALUES(?,?,?,?)',(n,a,b,g))
      refresh()
     except (ValueError,sqlite3.IntegrityError):messagebox.showerror('تحقق من البيانات','تأكد من الاسم والوقت والدقائق وعدم تكرار الاسم.')
    def choose(_):
     if t.selection():
      v=t.item(t.selection()[0],'values');sel['id']=int(v[0]);
      for k,x in zip(('name','start','end','grace'),v[1:]):fs[k].delete(0,'end');fs[k].insert(0,x)
    def delete():
     if not sel['id']:messagebox.showwarning('تنبيه','حدد وردية أولاً.');return
     if not messagebox.askyesno('تأكيد','حذف الوردية المحددة؟ لن يمكن حذف وردية مرتبطة بموظفين أو سجلات حضور.'):return
     try:
      with connect() as c:c.execute('DELETE FROM shifts WHERE id=?',(sel['id'],))
      self.audit('حذف وردية',str(sel['id']));clear();refresh()
     except sqlite3.IntegrityError:messagebox.showerror('تعذر الحذف','هذه الوردية مرتبطة بموظفين أو سجلات حضور. غيّر ارتباطاتها أولاً.')
    row=tk.Frame(self.area,bg=C['cream']);row.pack(fill='x');self.btn(row,'حفظ / تحديث',save,C['green']).pack(side='right',padx=4);self.btn(row,'جديد',clear,C['navy2']).pack(side='right',padx=4);self.btn(row,'حذف',delete,C['red']).pack(side='right',padx=4);t.bind('<<TreeviewSelect>>',choose);refresh()
 def employees(self):
    self.page('إدارة الموظفين','إضافة الموظفين وتعديل بياناتهم وحذفهم، مع البحث والفلترة حسب القسم.')
    with connect() as c:
     deps=c.execute('SELECT id,name FROM departments ORDER BY name').fetchall()
     sh=c.execute('SELECT id,name FROM shifts ORDER BY name').fetchall()
    dm={r['name']:r['id'] for r in deps};sm={r['name']:r['id'] for r in sh}
    f=self.panel(self.area,'بيانات الموظف');f.pack(fill='x')
    fs={};spec=[('code','الرقم الوظيفي'),('name','اسم الموظف'),('phone','رقم الهاتف'),('dept','القسم'),('shift','الوردية'),('hire','تاريخ التعيين'),('status','الحالة')]
    for i,(k,l) in enumerate(spec):
     b=tk.Frame(f,bg=C['paper']);b.grid(row=i//4,column=3-i%4,padx=6,pady=4,sticky='ew')
     self.label(b,l,font=('Arial',9,'bold')).pack(anchor='e')
     if k=='dept':w=ttk.Combobox(b,values=list(dm),state='readonly',justify='right')
     elif k=='shift':w=ttk.Combobox(b,values=list(sm),state='readonly',justify='right')
     elif k=='status':w=ttk.Combobox(b,values=['نشط','غير نشط'],state='readonly',justify='right');w.set('نشط')
     else:w=self.entry(b,19)
     w.pack(fill='x',ipady=5,pady=4);fs[k]=w;f.columnconfigure(i%4,weight=1)

    bar=tk.Frame(self.area,bg=C['cream']);bar.pack(fill='x',pady=(10,2))
    self.label(bar,'القسم:',bg=C['cream'],font=('Arial',10,'bold')).pack(side='right',padx=5)
    filt=ttk.Combobox(bar,values=['كل الأقسام']+list(dm),state='readonly',width=23,justify='right')
    filt.set('كل الأقسام');filt.pack(side='right',padx=5)
    search=self.entry(bar,25);search.pack(side='right',ipady=5,padx=5)
    search.insert(0,'ابحث بالاسم أو الرقم')
    sel={'id':None}

    def refresh():
     t.delete(*t.get_children())
     q=search.get().strip();q='' if q=='ابحث بالاسم أو الرقم' else q
     sql='''SELECT e.id,e.emp_code,e.full_name,e.phone,COALESCE(d.name,'—'),COALESCE(s.name,'—'),e.hire_date,e.status
            FROM employees e LEFT JOIN departments d ON d.id=e.department_id
            LEFT JOIN shifts s ON s.id=e.shift_id
            WHERE (e.full_name LIKE ? OR e.emp_code LIKE ?)'''
     args=['%'+q+'%','%'+q+'%']
     if filt.get() not in ('','كل الأقسام'):
      sql+=' AND d.name=?';args.append(filt.get())
     sql+=' ORDER BY e.full_name COLLATE NOCASE'
     with connect() as c:rs=c.execute(sql,args).fetchall()
     for r in rs:t.insert('','end',iid=str(r['id']),values=tuple(r))

    def clear():
     sel['id']=None
     for k,w in fs.items():
      if isinstance(w,ttk.Combobox):w.set('نشط' if k=='status' else '')
      else:w.delete(0,'end')
     t.selection_remove(t.selection())
     self.employee_mode_label.configure(text='الوضع الحالي: إضافة موظف جديد')

    def collect_values():
     code=fs['code'].get().strip();name=fs['name'].get().strip();hire=fs['hire'].get().strip()
     if not code or not name:
      messagebox.showwarning('بيانات ناقصة','الرقم الوظيفي واسم الموظف مطلوبان.');return None
     if hire:
      try:valid_date(hire)
      except ValueError:
       messagebox.showerror('تاريخ غير صحيح','اكتب تاريخ التعيين بالصيغة YYYY-MM-DD.');return None
     return (code,name,fs['phone'].get().strip(),dm.get(fs['dept'].get()),sm.get(fs['shift'].get()),hire,fs['status'].get() or 'نشط')

    def save_new():
     if sel['id'] is not None:
      messagebox.showwarning('حدد الإجراء','أنت تعدّل موظفًا محددًا. اضغط «تعديل الموظف المحدد» لحفظ التعديلات، أو «موظف جديد» لإضافة سجل جديد.')
      return
     vals=collect_values()
     if vals is None:return
     try:
      with connect() as c:
       c.execute('INSERT INTO employees(emp_code,full_name,phone,department_id,shift_id,hire_date,status) VALUES(?,?,?,?,?,?,?)',vals)
      self.audit('إضافة موظف',vals[0]);clear();refresh()
      messagebox.showinfo('تم الحفظ','تمت إضافة الموظف بنجاح.')
     except sqlite3.IntegrityError:
      messagebox.showerror('تعذر الحفظ','الرقم الوظيفي مستخدم مسبقًا. اختر رقمًا آخر.')

    def update_selected():
     if sel['id'] is None:
      messagebox.showwarning('لم يتم تحديد موظف','اختر موظفًا من الجدول أولًا، ثم عدّل الاسم أو الرقم أو أي بيانات أخرى واضغط «تعديل الموظف المحدد».')
      return
     vals=collect_values()
     if vals is None:return
     try:
      with connect() as c:
       exists=c.execute('SELECT id FROM employees WHERE id=?',(sel['id'],)).fetchone()
       if not exists:
        messagebox.showerror('تعذر التعديل','الموظف المحدد لم يعد موجودًا. حدّث القائمة وحاول مرة أخرى.');clear();refresh();return
       c.execute('UPDATE employees SET emp_code=?,full_name=?,phone=?,department_id=?,shift_id=?,hire_date=?,status=? WHERE id=?',vals+(sel['id'],))
      self.audit('تعديل بيانات موظف',f"ID={sel['id']} | الرقم={vals[0]}")
      clear();refresh();messagebox.showinfo('تم التعديل','تم تحديث بيانات الموظف بنجاح.')
     except sqlite3.IntegrityError:
      messagebox.showerror('تعذر التعديل','الرقم الوظيفي مستخدم لموظف آخر. اختر رقمًا مختلفًا.')

    def choose(_=None):
     if not t.selection():return
     v=t.item(t.selection()[0],'values')
     if not v:return
     sel['id']=int(v[0]);vals=v[1:]
     for k,x in zip(('code','name','phone','dept','shift','hire','status'),vals):
      if isinstance(fs[k],ttk.Combobox):fs[k].set('' if x=='—' else x)
      else:fs[k].delete(0,'end');fs[k].insert(0,x)
     self.employee_mode_label.configure(text='الوضع الحالي: تعديل الموظف المحدد — عدّل البيانات ثم اضغط زر التعديل')

    def delete_selected():
     if sel['id'] is None:
      messagebox.showwarning('لم يتم تحديد موظف','اختر الموظف الذي تريد حذفه من الجدول أولًا.');return
     emp_id=sel['id'];emp_name=fs['name'].get().strip();emp_code=fs['code'].get().strip()
     with connect() as c:
      att_count=c.execute('SELECT COUNT(*) FROM attendance WHERE employee_id=?',(emp_id,)).fetchone()[0]
      leave_count=c.execute('SELECT COUNT(*) FROM leave_requests WHERE employee_id=?',(emp_id,)).fetchone()[0]
     warning='هل تريد حذف الموظف '+emp_name+' (الرقم '+emp_code+') نهائيًا؟\n\n'
     warning+='سيتم حذف سجلات حضوره ('+str(att_count)+') وطلبات إجازاته ('+str(leave_count)+') أيضًا.\nلا يمكن التراجع عن هذا الإجراء.'
     if not messagebox.askyesno('تأكيد الحذف النهائي',warning,icon='warning'):return
     try:
      with connect() as c:
       c.execute('DELETE FROM attendance WHERE employee_id=?',(emp_id,))
       c.execute('DELETE FROM leave_requests WHERE employee_id=?',(emp_id,))
       c.execute('DELETE FROM employees WHERE id=?',(emp_id,))
      self.audit('حذف موظف نهائيًا',f'ID={emp_id} | الرقم={emp_code} | الاسم={emp_name}')
      clear();refresh();messagebox.showinfo('تم الحذف','تم حذف الموظف وسجلاته المرتبطة نهائيًا.')
     except sqlite3.IntegrityError as e:
      messagebox.showerror('تعذر الحذف','توجد بيانات مرتبطة تمنع الحذف. لم يتم حذف الموظف.\n'+str(e))

    self.employee_mode_label=self.label(self.area,'الوضع الحالي: إضافة موظف جديد',bg=C['cream'],fg=C['navy'],font=('Arial',10,'bold'))
    self.employee_mode_label.pack(anchor='e',pady=(5,2))
    actions=tk.Frame(self.area,bg=C['cream']);actions.pack(fill='x',pady=5)
    action_top=tk.Frame(actions,bg=C['cream']);action_top.pack(fill='x',pady=2)
    self.btn(action_top,'حفظ موظف جديد',save_new,C['green']).pack(side='right',padx=4)
    self.btn(action_top,'تعديل الموظف المحدد',update_selected,C['orange']).pack(side='right',padx=4)
    self.btn(action_top,'حذف الموظف المحدد',delete_selected,C['red']).pack(side='right',padx=4)
    self.btn(action_top,'تفريغ الحقول / موظف جديد',clear,C['navy2']).pack(side='right',padx=4)
    action_bottom=tk.Frame(actions,bg=C['cream']);action_bottom.pack(fill='x',pady=2)
    self.btn(action_bottom,'تطبيق البحث والفلترة',refresh,C['gold']).pack(side='right',padx=4)
    self.label(action_bottom,'للتعديل: اختر الموظف من الجدول، غيّر بياناته، ثم اضغط «تعديل الموظف المحدد».',bg=C['cream'],fg=C['muted'],font=('Arial',9)).pack(side='right',padx=8)
    # إنشاء الجدول بعد أزرار الإجراءات حتى لا يستهلك مساحة الواجهة ويحجبها.
    t=self.tree(self.area,('id','code','name','phone','dept','shift','hire','status'),('ID','الرقم الوظيفي','اسم الموظف','الهاتف','القسم','الوردية','التعيين','الحالة'))
    filt.bind('<<ComboboxSelected>>',lambda e:refresh());search.bind('<Return>',lambda e:refresh())
    t.bind('<<TreeviewSelect>>',choose);t.bind('<Double-1>',choose)
    refresh()
 def dashboard(self):
    self.page('لوحة التحكم','نظرة عامة على حركة الموظفين لهذا اليوم')
    today=date.today().isoformat()
    with connect() as c:
     total=c.execute("SELECT COUNT(*) FROM employees WHERE status='نشط' AND (hire_date='' OR hire_date IS NULL OR hire_date<=?)",(today,)).fetchone()[0]
     present_ids={r[0] for r in c.execute("SELECT DISTINCT a.employee_id FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE e.status='نشط' AND (e.hire_date='' OR e.hire_date IS NULL OR e.hire_date<=?) AND a.work_date=? AND a.check_in IS NOT NULL",(today,today)).fetchall()}
     present=len(present_ids)
     open_count=c.execute("SELECT COUNT(DISTINCT a.employee_id) FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE e.status='نشط' AND a.work_date BETWEEN ? AND ? AND a.check_in IS NOT NULL AND a.check_out IS NULL",((date.today()-timedelta(days=1)).isoformat(),today)).fetchone()[0]
     pending=c.execute("SELECT COUNT(*) FROM leave_requests WHERE status='قيد المراجعة'").fetchone()[0];deps=c.execute('SELECT COUNT(*) FROM departments').fetchone()[0]
     workdays={r['weekday']:r['is_workday'] for r in c.execute('SELECT weekday,is_workday FROM workdays')};holidays={r['holiday_date'] for r in c.execute('SELECT holiday_date FROM holidays')}
     onleave={r['employee_id'] for r in c.execute("SELECT l.employee_id FROM leave_requests l JOIN employees e ON e.id=l.employee_id WHERE e.status='نشط' AND (e.hire_date='' OR e.hire_date IS NULL OR e.hire_date<=?) AND l.status='موافق' AND l.start_date<=? AND l.end_date>=?",(today,today,today)).fetchall()}
    absent=max(0,total-len(present_ids | onleave)) if is_workday(date.today(),workdays,holidays) else 0
    cards=tk.Frame(self.area,bg=C['cream']);cards.pack(fill='x',pady=18)
    for i,(lab,val,col) in enumerate([('الموظفون النشطون',total,C['navy']),('الحضور اليوم',present,C['green']),('الغياب اليوم',absent,C['red']),('انصراف غير مسجل',open_count,C['orange']),('طلبات إجازة معلقة',pending,C['red']),('الأقسام',deps,C['navy2'])]):
     card=tk.Frame(cards,bg=C['paper'],highlightbackground=C['line'],highlightthickness=1);card.grid(row=0,column=i,sticky='nsew',padx=5);tk.Frame(card,bg=col,height=5).pack(fill='x');self.label(card,lab,font=('Arial',10,'bold'),fg=C['muted']).pack(anchor='e',padx=12,pady=(15,6));self.label(card,str(val),font=('Arial',25,'bold'),fg=C['navy']).pack(anchor='e',padx=12,pady=(0,16));cards.columnconfigure(i,weight=1)
    quick=self.panel(self.area,'اختصارات سريعة');quick.pack(fill='x',pady=10)
    shortcuts=[('تسجيل حركة موظف',self.punch),('عرض سجل اليوم',self.daily)]
    if self.role in ('مدير','مسؤول'):shortcuts += [('التقرير الشهري',self.reports),('إضافة موظف',self.employees)]
    for txt,fn in shortcuts:self.btn(quick,txt,fn,C['navy2']).pack(side='right',padx=6,pady=5)
    self.label(self.area,'ملاحظة: يمكنك إدخال وقت الحضور والانصراف يدويًا بصيغة 12 ساعة، ويحسب النظام ساعات العمل والتأخير.',bg=C['cream'],fg=C['muted']).pack(anchor='e',pady=12)
 def find_employee(self,text):
    q=text.strip()
    if not q:return None
    with connect() as c:
     r=c.execute("SELECT id,emp_code,full_name,status FROM employees WHERE (emp_code=? OR CAST(id AS TEXT)=? OR full_name=?) LIMIT 1",(q,q,q)).fetchone()
     if not r:r=c.execute("SELECT id,emp_code,full_name,status FROM employees WHERE full_name LIKE ? OR emp_code LIKE ? ORDER BY full_name LIMIT 1",('%'+q+'%','%'+q+'%')).fetchone()
    return r
 def punch(self):
    self.page('تسجيل الحضور والانصراف','ابحث بالاسم أو الرقم الوظيفي أو رقم الموظف. اختر الوردية المطلوبة عند تسجيل الحضور.')
    with connect() as c:shift_rows=c.execute('SELECT id,name FROM shifts ORDER BY name').fetchall()
    shift_choices={r['name']:r['id'] for r in shift_rows}
    f=self.panel(self.area,'بيانات الحركة');f.pack(fill='x',pady=6)
    self.label(f,'اسم الموظف / الرقم الوظيفي / ID',font=('Arial',10,'bold')).grid(row=0,column=3,sticky='e',padx=8);who=self.entry(f,32);who.grid(row=1,column=3,padx=8,pady=6,ipady=7,sticky='ew')
    self.label(f,'تاريخ الحركة YYYY-MM-DD',font=('Arial',10,'bold')).grid(row=0,column=2,sticky='e',padx=8);day=self.entry(f,16);day.insert(0,date.today().isoformat());day.grid(row=1,column=2,padx=8,pady=6,ipady=7)
    self.label(f,'الوقت (12 ساعة)',font=('Arial',10,'bold')).grid(row=0,column=1,sticky='e',padx=8);tm=self.entry(f,17);tm.insert(0,datetime.now().strftime('%I:%M %p'));tm.grid(row=1,column=1,padx=8,pady=6,ipady=7)
    for i in range(4):f.columnconfigure(i,weight=1)
    info=tk.StringVar(value='لم يتم اختيار موظف بعد');self.label(f,'الوردية',font=('Arial',10,'bold')).grid(row=2,column=3,sticky='e',padx=8);shift_box=ttk.Combobox(f,values=list(shift_choices),state='readonly',justify='right',width=24);shift_box.grid(row=3,column=3,padx=8,pady=5,sticky='ew')
    if shift_choices:shift_box.current(0)
    tk.Label(f,textvariable=info,bg=C['paper'],fg=C['green'],font=('Arial',12,'bold')).grid(row=3,column=0,columnspan=3,pady=12)
    chosen={'employee':None}
    def lookup():
     r=self.find_employee(who.get());chosen['employee']=r
     if not r:info.set('لم يتم العثور على الموظف');return
     if r['status']!='نشط':info.set('الموظف غير نشط — لا يمكن تسجيل الحركة');return
     info.set(f"الموظف: {r['full_name']}   |   الرقم الوظيفي: {r['emp_code']}   |   ID: {r['id']}")
    self.btn(f,'بحث عن الموظف',lookup,C['navy2']).grid(row=1,column=0,padx=8,pady=5)
    def mark(kind):
     r=chosen['employee'] or self.find_employee(who.get())
     if not r:messagebox.showerror('الموظف غير موجود','ابحث عن موظف صحيح أولاً.');return
     if r['status']!='نشط':messagebox.showerror('موظف غير نشط','لا يمكن تسجيل حركة لموظف غير نشط.');return
     try:
      d=valid_date(day.get());hhmm=parse_time12(tm.get());stamp=f'{d.isoformat()} {hhmm}:00'
     except ValueError as ex:messagebox.showerror('بيانات غير صحيحة',str(ex));return
     with connect() as c:
      if kind=='in':
       empdata=c.execute('SELECT shift_id FROM employees WHERE id=?',(r['id'],)).fetchone();sid=shift_choices.get(shift_box.get(), empdata['shift_id'] if empdata else None)
       if sid is None:messagebox.showerror('الوردية مطلوبة','أنشئ وردية أو اربط الموظف بورديه قبل تسجيل الحضور.');return
       old=c.execute('SELECT * FROM attendance WHERE employee_id=? AND work_date=? AND shift_id IS ?',(r['id'],d.isoformat(),sid)).fetchone()
       if old and old['check_in']:messagebox.showwarning('مسجل مسبقًا','يوجد تسجيل حضور لهذا الموظف في هذا التاريخ.');return
       if old:c.execute('UPDATE attendance SET check_in=? WHERE id=?',(stamp,old['id']))
       else:c.execute('INSERT INTO attendance(employee_id,work_date,check_in,shift_id) VALUES(?,?,?,?)',(r['id'],d.isoformat(),stamp,sid))
      else:
       # يسمح بالانصراف على سجل مفتوح من نفس اليوم أو اليوم السابق (وردية ليلية)
       candidates=c.execute('SELECT * FROM attendance WHERE employee_id=? AND check_in IS NOT NULL AND check_out IS NULL AND work_date BETWEEN ? AND ? ORDER BY work_date DESC,id DESC',(r['id'],(d-timedelta(days=1)).isoformat(),d.isoformat())).fetchall()
       target=None
       for cand in candidates:
        start=datetime.strptime(cand['check_in'],'%Y-%m-%d %H:%M:%S')
        outdt=datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S')
        if outdt>=start or (start.date()==d and outdt<start):target=cand;break
       if not target:messagebox.showwarning('لا يوجد حضور مفتوح','لم نجد تسجيل حضور مفتوحًا في هذا اليوم أو اليوم السابق.');return
       outdt=datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S');indt=datetime.strptime(target['check_in'],'%Y-%m-%d %H:%M:%S')
       if outdt<indt:outdt+=timedelta(days=1);stamp=outdt.strftime('%Y-%m-%d %H:%M:%S')
       c.execute('UPDATE attendance SET check_out=? WHERE id=?',(stamp,target['id']))
     messagebox.showinfo('تم الحفظ',f"تم تسجيل {'الحضور' if kind=='in' else 'الانصراف'} للموظف {r['full_name']}\n{display12(stamp)}")
     info.set(f"آخر حركة محفوظة: {r['full_name']} — {display12(stamp)}")
    row=tk.Frame(self.area,bg=C['cream']);row.pack(fill='x',pady=10)
    self.btn(row,'تسجيل الحضور بالوقت المحدد',lambda:mark('in'),C['green']).pack(side='right',padx=5);self.btn(row,'تسجيل الانصراف بالوقت المحدد',lambda:mark('out'),C['orange']).pack(side='right',padx=5)
    who.bind('<Return>',lambda e:lookup())
    self.label(self.area,'مثال للوقت: 07:45 AM صباحًا أو 05:10 PM مساءً. لا يضع النظام الوقت تلقائيًا؛ أنت تحدده.',bg=C['cream'],fg=C['muted']).pack(anchor='e',pady=8)
 def attendance_rows(self,day):
    d=valid_date(day)
    with connect() as c:
     em=c.execute("SELECT e.id,e.emp_code,e.full_name,s.start_time,s.grace_minutes,s.name shift FROM employees e LEFT JOIN shifts s ON s.id=e.shift_id WHERE e.status='نشط' ORDER BY e.full_name",).fetchall()
     atts=c.execute('SELECT a.*,s.name shift_name,s.start_time,s.end_time,s.grace_minutes FROM attendance a LEFT JOIN shifts s ON s.id=a.shift_id WHERE a.work_date=? ORDER BY a.employee_id,a.id',(day,)).fetchall()
     leaves={r['employee_id'] for r in c.execute("SELECT employee_id FROM leave_requests WHERE status='موافق' AND start_date<=? AND end_date>=?",(day,day)).fetchall()}
     workdays={r['weekday']:r['is_workday'] for r in c.execute('SELECT weekday,is_workday FROM workdays')};holidays={r['holiday_date'] for r in c.execute('SELECT holiday_date FROM holidays')}
    grouped={}
    for a in atts:grouped.setdefault(a['employee_id'],[]).append(a)
    rows=[]
    for e in em:
     records=grouped.get(e['id'],[])
     if records:
      for a in records:
       inn=a['check_in'] or '';out=a['check_out'] or '';state='حاضر' if out else ('لم يسجل الانصراف' if inn else 'سجل فارغ');late='—';early='—';overtime='—';hrs='—'
       if e['id'] in leaves:state='إجازة'
       if inn:
        st=a['start_time'] or e['start_time']
        grace=a['grace_minutes'] if a['grace_minutes'] is not None else e['grace_minutes']
        if st:
         sch=datetime.strptime(day+' '+st,'%Y-%m-%d %H:%M')+timedelta(minutes=grace or 0);act=datetime.strptime(inn,'%Y-%m-%d %H:%M:%S');late=max(0,int((act-sch).total_seconds()/60))
        if out:
         hrs=hours(duration(inn,out))
         end_time=a['end_time']
         if end_time:
          scheduled_end=datetime.strptime(day+' '+end_time,'%Y-%m-%d %H:%M')
          start_time=a['start_time'] or e['start_time']
          if start_time and end_time<=start_time:scheduled_end+=timedelta(days=1)
          actual_out=datetime.strptime(out,'%Y-%m-%d %H:%M:%S')
          early=max(0,int((scheduled_end-actual_out).total_seconds()/60));overtime=max(0,int((actual_out-scheduled_end).total_seconds()/60))
       rows.append((e['id'],a['id'],e['emp_code'],e['full_name'],a['shift_name'] or e['shift'] or '—',display12(inn),display12(out),hrs,late,early,overtime,state))
     else:
      state='إجازة' if e['id'] in leaves else ('غياب' if is_workday(d,workdays,holidays) else 'عطلة')
      rows.append((e['id'],0,e['emp_code'],e['full_name'],e['shift'] or '—','—','—','—','—','—','—',state))
    return rows
 def daily(self):
    self.page('سجل الحضور اليومي','اختر التاريخ لعرض كل الموظفين وحالة الحضور وساعات العمل والتأخير. يمكنك تعديل أي سجل يدويًا.')
    bar=tk.Frame(self.area,bg=C['cream']);bar.pack(fill='x');self.label(bar,'التاريخ',bg=C['cream'],font=('Arial',10,'bold')).pack(side='right',padx=5);d=self.entry(bar,14);d.insert(0,date.today().isoformat());d.pack(side='right',ipady=5)
    t=self.tree(self.area,('id','attid','code','name','shift','in','out','hours','late','early','overtime','state'),('ID الموظف','ID السجل','الرقم الوظيفي','الموظف','الوردية','الحضور','الانصراف','الساعات','التأخير/دقيقة','الخروج المبكر/دقيقة','الإضافي/دقيقة','الحالة'))
    def refresh():
     try:valid_date(d.get())
     except ValueError:messagebox.showerror('تاريخ غير صحيح','استخدم YYYY-MM-DD');return
     t.delete(*t.get_children())
     for r in self.attendance_rows(d.get()):t.insert('','end',values=r)
    def edit():
     if not t.selection():messagebox.showwarning('تنبيه','حدد سجل موظف من الجدول.');return
     v=t.item(t.selection()[0],'values');empid=int(v[0]);attid=int(v[1]);empname=v[3]
     with connect() as c:a=c.execute('SELECT * FROM attendance WHERE id=?',(attid,)).fetchone() if attid else None
     win=tk.Toplevel(self);win.title('تعديل سجل الحضور');win.configure(bg=C['cream']);win.geometry('440x330');win.transient(self);win.grab_set()
     self.label(win,'تعديل سجل: '+empname,bg=C['cream'],font=('Arial',14,'bold'),fg=C['navy']).pack(pady=15)
     fields={}
     for k,l,val in [('in','وقت الحضور (12 ساعة)',display12(a['check_in']) if a else ''),('out','وقت الانصراف (12 ساعة)',display12(a['check_out']) if a else ''),('notes','ملاحظات',a['notes'] if a else '')]:
      self.label(win,l,bg=C['cream'],font=('Arial',10,'bold')).pack(anchor='e',padx=25);e=self.entry(win,28);e.insert(0,'' if val=='—' else val);e.pack(padx=20,ipady=6,pady=4);fields[k]=e
     def save():
      try:
       inn=parse_time12(fields['in'].get()) if fields['in'].get().strip() else None;out=parse_time12(fields['out'].get()) if fields['out'].get().strip() else None
       ins=f"{d.get()} {inn}:00" if inn else None;outs=f"{d.get()} {out}:00" if out else None
       if ins and outs and datetime.strptime(outs,'%Y-%m-%d %H:%M:%S')<datetime.strptime(ins,'%Y-%m-%d %H:%M:%S'):outs=(datetime.strptime(outs,'%Y-%m-%d %H:%M:%S')+timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
       with connect() as c:
        if a:c.execute('UPDATE attendance SET check_in=?,check_out=?,notes=? WHERE id=?',(ins,outs,fields['notes'].get(),a['id']))
        elif ins or outs:
         emp=c.execute('SELECT shift_id FROM employees WHERE id=?',(empid,)).fetchone();c.execute('INSERT INTO attendance(employee_id,work_date,check_in,check_out,notes,shift_id) VALUES(?,?,?,?,?,?)',(empid,d.get(),ins,outs,fields['notes'].get(),emp['shift_id'] if emp else None))
       self.audit('تصحيح سجل حضور',f'الموظف {empid} بتاريخ {d.get()}')
       win.destroy();refresh()
      except ValueError as ex:messagebox.showerror('وقت غير صحيح',str(ex),parent=win)
     self.btn(win,'حفظ التعديل',save,C['green']).pack(pady=12)
    self.btn(bar,'عرض السجل',refresh,C['navy2']).pack(side='right',padx=5);self.btn(bar,'تعديل السجل المحدد',edit,C['orange']).pack(side='right',padx=5);self.btn(bar,'تصدير Excel',lambda:self.export_table(t,'سجل_الحضور', 'xlsx'),C['green']).pack(side='left',padx=4);self.btn(bar,'تصدير PDF',lambda:self.export_table(t,'سجل_الحضور','pdf'),C['red']).pack(side='left',padx=4);self.btn(bar,'تصدير CSV',lambda:self.export_table(t,'سجل_الحضور','csv'),C['navy']).pack(side='left',padx=4)
    t.bind('<Double-1>',lambda e:edit());refresh()
 def leaves(self):
    self.page('إدارة الإجازات','إدارة طلبات الإجازات ومراجعتها بالموافقة أو الرفض، مع إمكانية تعديل الطلب وحذفه.')
    with connect() as c:emps=c.execute('SELECT id,emp_code,full_name FROM employees ORDER BY full_name').fetchall()
    choices={f"{e['emp_code']} — {e['full_name']}":e['id'] for e in emps}
    f=self.panel(self.area,'تفاصيل طلب الإجازة');f.pack(fill='x');fs={};spec=[('employee','الموظف'),('start','من تاريخ'),('end','إلى تاريخ'),('type','نوع الإجازة'),('reason','السبب'),('status','حالة الطلب')]
    for i,(k,l) in enumerate(spec):
     b=tk.Frame(f,bg=C['paper']);b.grid(row=0,column=5-i,padx=6,sticky='ew');self.label(b,l,font=('Arial',9,'bold')).pack(anchor='e')
     if k=='employee':w=ttk.Combobox(b,values=list(choices),width=24,justify='right')
     elif k=='type':w=ttk.Combobox(b,values=['سنوية','مرضية','طارئة','بدون راتب','إذن'],state='readonly',justify='right');w.set('سنوية')
     elif k=='status':w=ttk.Combobox(b,values=['قيد المراجعة','موافق','مرفوض'],state='readonly',justify='right');w.set('قيد المراجعة')
     else:w=self.entry(b,20);w.insert(0,date.today().isoformat() if k in ('start','end') else '')
     w.pack(fill='x',ipady=6,pady=5);fs[k]=w;f.columnconfigure(i,weight=1)
    t=self.tree(self.area,('id','employee','start','end','type','reason','status'),('ID','الموظف','من تاريخ','إلى تاريخ','النوع','السبب','الحالة'))
    sel={'id':None}
    def refresh():
     t.delete(*t.get_children())
     with connect() as c:rs=c.execute('SELECT l.*,e.emp_code,e.full_name FROM leave_requests l JOIN employees e ON e.id=l.employee_id ORDER BY l.start_date DESC,l.id DESC').fetchall()
     for r in rs:t.insert('','end',iid=str(r['id']),values=(r['id'],r['emp_code']+' — '+r['full_name'],r['start_date'],r['end_date'],r['leave_type'],r['reason'],r['status']))
    def clear():
     sel['id']=None
     for k,w in fs.items():
      if isinstance(w,ttk.Combobox):w.set('سنوية' if k=='type' else ('قيد المراجعة' if k=='status' else ''))
      else:w.delete(0,'end');w.insert(0,date.today().isoformat() if k in ('start','end') else '')
     t.selection_remove(t.selection())
    def save():
     try:
      a=valid_date(fs['start'].get());b=valid_date(fs['end'].get())
      if b<a:raise ValueError('تاريخ النهاية يجب ألا يسبق تاريخ البداية.')
      emp=choices.get(fs['employee'].get())
      if not emp:raise ValueError('اختر موظفًا من القائمة.')
      vals=(emp,a.isoformat(),b.isoformat(),fs['type'].get(),fs['reason'].get().strip(),fs['status'].get())
      with connect() as c:
       overlap=c.execute("SELECT id FROM leave_requests WHERE employee_id=? AND status!='مرفوض' AND start_date<=? AND end_date>=? AND id!=? LIMIT 1",(emp,b.isoformat(),a.isoformat(),sel['id'] or -1)).fetchone()
       if overlap:raise ValueError('توجد إجازة أخرى متداخلة لهذا الموظف؛ راجع الطلبات السابقة.')
       if sel['id']:c.execute('UPDATE leave_requests SET employee_id=?,start_date=?,end_date=?,leave_type=?,reason=?,status=? WHERE id=?',vals+(sel['id'],))
       else:c.execute('INSERT INTO leave_requests(employee_id,start_date,end_date,leave_type,reason,status) VALUES(?,?,?,?,?,?)',vals)
      self.audit('حفظ طلب إجازة',str(sel['id'] or 'جديد'));clear();refresh()
     except ValueError as ex:messagebox.showerror('تحقق من الطلب',str(ex))
    def choose(_):
     if not t.selection():return
     v=t.item(t.selection()[0],'values');sel['id']=int(v[0]);fs['employee'].set(v[1]);fs['start'].delete(0,'end');fs['start'].insert(0,v[2]);fs['end'].delete(0,'end');fs['end'].insert(0,v[3]);fs['type'].set(v[4]);fs['reason'].delete(0,'end');fs['reason'].insert(0,v[5]);fs['status'].set(v[6])
    def delete():
     if not sel['id']:messagebox.showwarning('تنبيه','حدد طلبًا أولاً.');return
     if messagebox.askyesno('تأكيد','هل تريد حذف طلب الإجازة المحدد؟'):
      with connect() as c:c.execute('DELETE FROM leave_requests WHERE id=?',(sel['id'],))
      clear();refresh()
    row=tk.Frame(self.area,bg=C['cream']);row.pack(fill='x',pady=5);self.btn(row,'حفظ / تحديث الطلب',save,C['green']).pack(side='right',padx=4);self.btn(row,'طلب جديد',clear,C['navy2']).pack(side='right',padx=4);self.btn(row,'حذف الطلب',delete,C['red']).pack(side='right',padx=4);t.bind('<<TreeviewSelect>>',choose);refresh()
 def report_data(self,month):
    start=datetime.strptime(month+'-01','%Y-%m-%d').date();end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1);count=end.day
    with connect() as c:
     em=c.execute("SELECT e.id,e.emp_code,e.full_name,e.hire_date,s.start_time,s.end_time,s.grace_minutes FROM employees e LEFT JOIN shifts s ON s.id=e.shift_id WHERE e.status='نشط' ORDER BY e.full_name").fetchall()
     atts=c.execute('SELECT a.*,s.start_time shift_start,s.end_time shift_end,s.grace_minutes shift_grace FROM attendance a LEFT JOIN shifts s ON s.id=a.shift_id WHERE a.work_date BETWEEN ? AND ? ORDER BY a.work_date,a.id',(start.isoformat(),end.isoformat())).fetchall()
     leaves=c.execute("SELECT * FROM leave_requests WHERE status='موافق' AND start_date<=? AND end_date>=?",(end.isoformat(),start.isoformat())).fetchall()
     workdays={r['weekday']:r['is_workday'] for r in c.execute('SELECT weekday,is_workday FROM workdays')}
     holidays={r['holiday_date'] for r in c.execute('SELECT holiday_date FROM holidays')}
    amap={}
    for a in atts: amap.setdefault((a['employee_id'],a['work_date']),[]).append(a)
    rows=[]
    for e in em:
     p=ab=lv=late=early=overtime=sec=0
     for n in range(count):
      day=start+timedelta(days=n);ds=day.isoformat()
      if e['hire_date'] and ds<e['hire_date']:continue
      if not is_workday(day,workdays,holidays):continue
      records=amap.get((e['id'],ds),[])
      onleave=any(l['employee_id']==e['id'] and l['start_date']<=ds<=l['end_date'] for l in leaves)
      if onleave:lv+=1
      elif any(a['check_in'] for a in records):
       p+=1
       for a in records:
        if a['check_in'] and a['check_out']:
         sec+=duration(a['check_in'],a['check_out'])
         end_time=a['shift_end'] or e['end_time']
         start_time=a['shift_start'] or e['start_time']
         if end_time:
          scheduled_end=datetime.strptime(ds+' '+end_time,'%Y-%m-%d %H:%M')
          if start_time and end_time<=start_time:scheduled_end+=timedelta(days=1)
          actual_out=datetime.strptime(a['check_out'],'%Y-%m-%d %H:%M:%S')
          early+=max(0,int((scheduled_end-actual_out).total_seconds()/60))
          overtime+=max(0,int((actual_out-scheduled_end).total_seconds()/60))
        if a['check_in'] and (a['shift_start'] or e['start_time']):
         shift_start=a['shift_start'] or e['start_time'];grace=a['shift_grace'] if a['shift_grace'] is not None else e['grace_minutes']
         sch=datetime.strptime(ds+' '+shift_start,'%Y-%m-%d %H:%M')+timedelta(minutes=grace or 0)
         actual=datetime.strptime(a['check_in'],'%Y-%m-%d %H:%M:%S')
         # لا تُحسب الوردية الليلية متأخرة إذا كان تاريخ تسجيلها هو تاريخ بدايتها.
         late+=max(0,int((actual-sch).total_seconds()/60))
      else:ab+=1
     rows.append((e['emp_code'],e['full_name'],p,ab,lv,late,early,overtime,hours(sec)))
    return rows
 def reports(self):
    self.page('التقارير الشهرية','ملخص شهري لكل موظف: الحضور والغياب والإجازات والتأخير وساعات العمل.')
    bar=tk.Frame(self.area,bg=C['cream']);bar.pack(fill='x');self.label(bar,'الشهر YYYY-MM',bg=C['cream'],font=('Arial',10,'bold')).pack(side='right',padx=5);m=self.entry(bar,12);m.insert(0,date.today().strftime('%Y-%m'));m.pack(side='right',ipady=5)
    summary=tk.Frame(self.area,bg=C['cream']);summary.pack(fill='x',pady=8);vars={k:tk.StringVar(value='0') for k in ('employees','present','absent','leave')}
    for i,(k,lab) in enumerate([('employees','عدد الموظفين'),('present','أيام الحضور'),('absent','أيام الغياب'),('leave','أيام الإجازة')]):
     x=tk.Frame(summary,bg=C['paper'],highlightbackground=C['line'],highlightthickness=1);x.grid(row=0,column=i,sticky='ew',padx=5);self.label(x,lab,fg=C['muted'],font=('Arial',9,'bold')).pack(padx=12,pady=(9,2));tk.Label(x,textvariable=vars[k],bg=C['paper'],fg=C['navy'],font=('Arial',19,'bold')).pack(pady=(0,8));summary.columnconfigure(i,weight=1)
    t=self.tree(self.area,('code','name','present','absent','leave','late','early','overtime','hours'),('الرقم الوظيفي','الموظف','أيام الحضور','أيام الغياب','الإجازات','التأخير بالدقائق','الخروج المبكر بالدقائق','الساعات الإضافية بالدقائق','ساعات العمل'))
    def make():
     try:datetime.strptime(m.get().strip()+'-01','%Y-%m-%d')
     except ValueError:messagebox.showerror('الشهر غير صحيح','اكتب الشهر بصيغة YYYY-MM');return
     rows=self.report_data(m.get().strip());t.delete(*t.get_children())
     for r in rows:t.insert('','end',values=r)
     vars['employees'].set(str(len(rows)));vars['present'].set(str(sum(int(r[2]) for r in rows)));vars['absent'].set(str(sum(int(r[3]) for r in rows)));vars['leave'].set(str(sum(int(r[4]) for r in rows)))
    self.btn(bar,'إنشاء التقرير',make,C['navy2']).pack(side='right',padx=6);self.btn(bar,'Excel',lambda:self.export_table(t,'التقرير_الشهري','xlsx'),C['green']).pack(side='left',padx=4);self.btn(bar,'PDF',lambda:self.export_table(t,'التقرير_الشهري','pdf'),C['red']).pack(side='left',padx=4);self.btn(bar,'CSV',lambda:self.export_table(t,'التقرير_الشهري','csv'),C['navy']).pack(side='left',padx=4);make()
 def export_table(self,t,default,kind):
    ext=kind;types={'xlsx':[('Excel Workbook','*.xlsx')],'pdf':[('PDF','*.pdf')],'csv':[('CSV','*.csv')]}
    path=filedialog.asksaveasfilename(defaultextension='.'+ext,initialfile=default+'.'+ext,filetypes=types[kind])
    if not path:return
    headers=[t.heading(c)['text'] for c in t['columns']];rows=[list(t.item(i,'values')) for i in t.get_children()]
    try:
     if kind=='csv':
      with open(path,'w',newline='',encoding='utf-8-sig') as f:w=csv.writer(f);w.writerow(headers);w.writerows(rows)
     elif kind=='xlsx':
      from openpyxl import Workbook
      from openpyxl.styles import Font,PatternFill,Border,Side,Alignment
      from openpyxl.utils import get_column_letter
      wb=Workbook();ws=wb.active;ws.title='التقرير';ws.sheet_view.rightToLeft=True;ws.append(['نظام إدارة الحضور والانصراف',datetime.now().strftime('%Y-%m-%d %I:%M %p')]);ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=len(headers));ws['A1'].font=Font(name='Arial',size=16,bold=True,color='FFFFFF');ws['A1'].fill=PatternFill('solid',fgColor='17263D');ws['A1'].alignment=Alignment(horizontal='center');ws.append(headers)
      for cell in ws[2]:cell.font=Font(name='Arial',bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='B58A45');cell.alignment=Alignment(horizontal='center',vertical='center')
      for row in rows:ws.append(row)
      thin=Side(style='thin',color='DED7C9')
      for row in ws.iter_rows(min_row=3):
       for cell in row:cell.font=Font(name='Arial',size=10,color='273449');cell.alignment=Alignment(horizontal='center',vertical='center');cell.border=Border(bottom=thin)
      for col in range(1,len(headers)+1):
       vals=[len(str(ws.cell(r,col).value or '')) for r in range(2,ws.max_row+1)];ws.column_dimensions[get_column_letter(col)].width=min(34,max(14,max(vals,default=10)+4))
      ws.freeze_panes='A3';ws.auto_filter.ref=f'A2:{get_column_letter(len(headers))}{ws.max_row}';wb.save(path)
     else:self.export_pdf(path,headers,rows,default)
     messagebox.showinfo('تم التصدير',f'تم إنشاء الملف بنجاح:\n{path}')
    except ImportError as ex:messagebox.showerror('مكتبة ناقصة','ثبّت المكتبات من ملف requirements.txt ثم أعد المحاولة.\n'+str(ex))
    except Exception as ex:messagebox.showerror('تعذر التصدير',str(ex))
 def export_pdf(self,path,headers,rows,title):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.enums import TA_CENTER
    try:
     import arabic_reshaper
     from bidi.algorithm import get_display
     shape=lambda s:get_display(arabic_reshaper.reshape(str(s)))
    except ImportError:shape=lambda s:str(s)
    fontpath=BASE/'DejaVuSans.ttf'
    if not fontpath.exists():
     candidates=[Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),Path('C:/Windows/Fonts/arial.ttf')]
     fontpath=next((p for p in candidates if p.exists()),None)
    if fontpath:
     try:pdfmetrics.registerFont(TTFont('Arabic',str(fontpath)));font='Arabic'
     except Exception:font='Helvetica'
    else:font='Helvetica'
    doc=SimpleDocTemplate(path,pagesize=landscape(A4),rightMargin=22,leftMargin=22,topMargin=24,bottomMargin=24)
    styles=getSampleStyleSheet();title_style=ParagraphStyle('ArTitle',parent=styles['Title'],fontName=font,fontSize=17,leading=24,alignment=TA_CENTER,textColor=colors.HexColor('#17263D'))
    story=[Paragraph(shape('نظام إدارة الحضور والانصراف — '+title),title_style),Spacer(1,12),Paragraph(shape('تاريخ إنشاء التقرير: '+datetime.now().strftime('%Y-%m-%d %I:%M %p')),ParagraphStyle('meta',parent=styles['Normal'],fontName=font,fontSize=8,alignment=TA_CENTER)),Spacer(1,10)]
    data=[[Paragraph(shape(x),ParagraphStyle('h',fontName=font,fontSize=8,textColor=colors.white,alignment=TA_CENTER)) for x in headers]]
    for row in rows:data.append([Paragraph(shape(x),ParagraphStyle('c',fontName=font,fontSize=7,alignment=TA_CENTER)) for x in row])
    widths=[(landscape(A4)[0]-44)/max(1,len(headers))]*len(headers)
    tab=Table(data,colWidths=widths,repeatRows=1,hAlign='CENTER')
    tab.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#17263D')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F4F0E7')]),('GRID',(0,0),(-1,-1),.45,colors.HexColor('#DED7C9')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    story.append(tab);doc.build(story)

def main():init_db();App().mainloop()
if __name__=='__main__':main()
