#ifndef slic3r_HMSPanel_hpp_
#define slic3r_HMSPanel_hpp_

#include <string>
#include <wx/event.h>
#include <wx/gdicmn.h>
#include <map>
#include <wx/panel.h>
#include <wx/scrolwin.h>
#include <wx/sizer.h>
#include <wx/textctrl.h>
#include <slic3r/GUI/Widgets/Button.hpp>
#include <slic3r/GUI/Widgets/CheckBox.hpp>
#include <slic3r/GUI/Widgets/Label.hpp>
#include <slic3r/GUI/DeviceManager.hpp>
#include <slic3r/GUI/Widgets/ScrolledWindow.hpp>
#include <slic3r/GUI/StatusPanel.hpp>
#include <wx/html/htmlwin.h>
#include <atomic>
#include <memory>
#include <utility>
#include <vector>

#include "DeviceCore/DevHMS.h"

namespace Slic3r {
namespace GUI {

class HMSNotifyItem : public wxPanel
{
    DevHMSItem &   m_hms_item;
    std::string m_url;
    std::string dev_id;
    std::string long_error_code;

    wxPanel *       m_panel_hms;
    wxStaticBitmap *m_bitmap_notify;
    wxStaticBitmap *m_bitmap_arrow;
    wxStaticText *  m_hms_content;
    wxPanel *       m_staticline;

    wxBitmap m_img_notify_lv1;
    wxBitmap m_img_notify_lv2;
    wxBitmap m_img_notify_lv3;
    wxBitmap m_img_arrow;

    void          init_bitmaps();
    wxBitmap &    get_notify_bitmap();

public:
     HMSNotifyItem(const std::string& dev_id, wxWindow *parent, DevHMSItem& item);
    ~HMSNotifyItem();

     void msw_rescale() {}
};


// Orca: a printer agent's log on the Assistant page (MachineObject::agent_log): the errors and warnings
// since the last Clear, the log's last lines, and a download of the whole log from the printer's Moonraker.
class AgentLogPanel : public wxPanel
{
public:
    explicit AgentLogPanel(wxWindow* parent);
    ~AgentLogPanel() override;

    void update(MachineObject* obj);
    // Whether it lists an error or warning newer than the ones shown while the page was on screen.
    bool has_unread() const;

private:
    void show_problems(const std::vector<MachineObject::AgentLogProblem>& problems);
    void show_lines();
    void download();

    std::string m_dev_id;
    std::string m_name;
    std::string m_url; // the log's address in the printer's Moonraker
    std::string m_api_key;
    std::vector<std::pair<char, std::string>>     m_lines;
    std::vector<MachineObject::AgentLogProblem>   m_problems;
    std::string                                   m_newest; // the newest problem listed
    std::string                                   m_read;   // the newest problem seen on screen

    Label*            m_problems_title;
    Button*           m_clear;
    Button*           m_download;
    wxScrolledWindow* m_problems_list;
    wxBoxSizer*       m_problems_sizer;
    Label*            m_log_title;
    ::CheckBox*       m_debug;
    wxTextCtrl*       m_log;

    std::shared_ptr<std::atomic<bool>> m_alive;
};

class HMSPanel : public wxPanel
{
protected:
    wxScrolledWindow *m_scrolledWindow;
    wxBoxSizer *      m_top_sizer;
    AgentLogPanel *   m_agent_log;

    int last_status;

    void append_hms_panel(const std::string& dev_id, DevHMSItem &item);
    void delete_hms_panels();


public:
    HMSPanel(wxWindow *parent, wxWindowID id = wxID_ANY, const wxPoint &pos = wxDefaultPosition, const wxSize &size = wxDefaultSize, long style = wxTAB_TRAVERSAL);
    ~HMSPanel();

    void msw_rescale() {}

    bool Show(bool show = true) override;

    void update(MachineObject *obj_);

    void show_status(int status);

    void clear_hms_tag();

    // Orca: an agent log error or warning not yet seen, for the page's new tag.
    bool has_unread_agent_log() const { return m_agent_log->IsShown() && m_agent_log->has_unread(); }

    MachineObject *obj { nullptr };
    std::map<std::string, DevHMSItem>    temp_hms_list;
};

wxDECLARE_EVENT(EVT_ALREADY_READ_HMS, wxCommandEvent);

}
}

#endif
