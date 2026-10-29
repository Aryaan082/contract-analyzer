// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract RewardPool {
    mapping(address => uint256) public pending;
    uint256 public totalPending;

    function fund(address user) external payable {
        pending[user] += msg.value;
        totalPending += msg.value;
    }

    function claim() external {
        uint256 owed = pending[msg.sender];
        require(owed > 0, "nothing owed");
        pending[msg.sender] = 0;
        totalPending -= owed;
        (bool sent, ) = msg.sender.call{value: owed}("");
        require(sent, "send failed");
    }
}
