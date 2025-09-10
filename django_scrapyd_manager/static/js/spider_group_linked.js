(function($) {
    $(function() {
        let $node = $("#id_node");
        let $project = $("#id_project");
        let $version = $("#id_version");
        let $spiders = $("#id_spiders_select");

        function loadProjects(nodeId) {
            $.ajax({
                url: `/admin/django_scrapyd_manager/spidergroup/api/node/${nodeId}/projects/`,
                success: function(data) {
                    $project.empty();
                    $project.append(new Option("---------", ""));
                    data.forEach(p => {
                        $project.append(new Option(p.text, p.id));
                    });
                    $project.trigger("change");  // 触发后续联动
                }
            });
        }

        function loadVersions(projectId) {
            $.ajax({
                url: `/admin/django_scrapyd_manager/spidergroup/api/project/${projectId}/versions/`,
                data: {
                    node_id: $node.val()
                },
                success: function(data) {
                    $version.empty();
                    let version_text = "自动最新版本"
                    if(data.length){
                        const latest_version = data[0];
                        version_text = `自动最新版[${latest_version.text}]`
                        $version.append(new Option(version_text, '', true))
                        // $version.val("0");
                        data.forEach(v => {
                            $version.append(new Option(v.text, v.id));
                        });
                    }else{
                        $version.append(new Option(`${version_text}[暂无可用版本]`, '0'))
                    }
                    $version.trigger("change");
                }
            });
        }

        function loadSpiders(versionId) {
            $.ajax({
                url: `/admin/django_scrapyd_manager/spidergroup/api/version/spiders/`,
                data: {
                    version_id: versionId,
                    project_id: $project.val(),
                    node_id: $node.val()
                },
                success: function(data) {
                    $spiders.empty();
                    data.forEach(s => {
                        $spiders.append(new Option(s.text, s.id));
                    });
                    // 重新让 admin 的过滤多选控件同步
                    // if (window.SelectFilter) {
                    //     SelectFilter.init("id_spiders_select", "爬虫", 0);
                    // }
                }
            });
        }

        $node.on("change", function() {
            let nodeId = $(this).val();
            if (nodeId) {
                loadProjects(nodeId);
            } else {
                $project.empty();
                $version.empty();
                $spiders.empty();
            }
        });

        $project.on("change", function() {
            let projectId = $(this).val();
            if (projectId) {
                if($version.length && !$version.is("input")){
                    loadVersions(projectId);
                }
            } else {
                $version.empty();
                $spiders.empty();
            }
        });

        // $version.on("change", function() {
        //     let versionId = $(this).val();
        //     if($spiders.length){
        //         loadSpiders(versionId);
        //     }
        //     // if (versionId) {
        //     //     if(versionId === '0'){
        //     //         versionId = undefined
        //     //     }
        //     //     loadSpiders(versionId);
        //     // } else {
        //     //     $spiders.empty();
        //     // }
        // });
    });
})(django.jQuery);