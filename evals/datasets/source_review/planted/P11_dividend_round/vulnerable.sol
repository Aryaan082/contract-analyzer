// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract DividendBook {
    address[] public holders;
    mapping(address => uint256) public stake;
    uint256 public totalStake;

    function join() external payable {
        require(msg.value > 0, "empty");
        if (stake[msg.sender] == 0) holders.push(msg.sender);
        stake[msg.sender] += msg.value;
        totalStake += msg.value;
    }

    function distribute() external {
        uint256 pot = address(this).balance - totalStake;
        for (uint256 i = 0; i < holders.length; i++) {
            address holder = holders[i];
            payable(holder).transfer(pot * stake[holder] / totalStake);
        }
    }
}
